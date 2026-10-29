import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import fastapi
import pytest
from src.services import sso_transactions as tx
from src.api.routes import auth_sso as routes


class Store:
    def __init__(self):
        self.data = {}
        self.expiry = {}

    async def set(self, key, value, ex, nx):
        if nx and key in self.data:
            return False
        self.data[key] = value
        self.expiry[key] = ex
        return True

    async def eval(self, script, count, key, expected):
        value = self.data.get(key)
        if value is None or value[:64] != expected:
            return None
        del self.data[key]
        return value[65:]


@pytest.mark.asyncio
async def test_independent_tabs_redeem_in_reverse_order():
    store = Store()
    a, ca = await tx.create(store, "/interview/1")
    b, cb = await tx.create(store, "/reports/2")
    assert a != b and ca != cb
    assert await tx.consume(store, b, cb) == {"destination": "/reports/2"}
    assert await tx.consume(store, a, ca) == {"destination": "/interview/1"}
    assert not store.data


@pytest.mark.asyncio
async def test_wrong_browser_cannot_consume_legitimate_transaction():
    store = Store()
    state, binding = await tx.create(store, "/practice")
    assert await tx.consume(store, state, "x" * 43) is None
    assert await tx.consume(store, state, binding) == {"destination": "/practice"}


@pytest.mark.asyncio
async def test_parallel_redeemers_have_one_winner_and_replay_fails():
    store = Store()
    state, binding = await tx.create(store, "/")
    results = await asyncio.gather(*(tx.consume(store, state, binding) for _ in range(20)))
    assert sum(result is not None for result in results) == 1
    assert await tx.consume(store, state, binding) is None


@pytest.mark.asyncio
async def test_secrets_not_stored_raw_and_expiry_is_bounded():
    store = Store()
    state, binding = await tx.create(store, "/practice")
    assert binding not in store.data[tx.PREFIX + state]
    assert store.expiry[tx.PREFIX + state] == 300


@pytest.mark.parametrize("path", ["https://evil.test", "//evil.test", "/%2Fevil.test", "/%252Fevil.test", "/\\evil.test", "/%0aevil", "/auth/signup", "/%61uth/callback", "/x/../api/logout", "/login", "relative", "/%zz", None])
def test_unsafe_return_paths_are_rejected(path):
    assert tx.safe_destination(path) == "/"


def test_valid_deep_link_retains_query_and_fragment():
    assert tx.frontend_destination("https://product.test/auth/signup", "/reports/2?view=all#score") == "https://product.test/reports/2?view=all#score"


@pytest.mark.asyncio
@pytest.mark.parametrize("state,binding", [(None, None), ("bad", "x"*43), ("x"*43,None), ("x"*43,"bad")])
async def test_invalid_transaction_credentials_fail_without_store_read(state, binding):
    store = Store()
    store.eval = AsyncMock(side_effect=AssertionError("must not read"))
    assert await tx.consume(store, state, binding) is None


@pytest.fixture
def setup(monkeypatch):
    config = SimpleNamespace(SSO_TRANSACTION_ENABLED=True, SSO_REDIRECT_URI="https://api.test/api/auth/sso/callback", AUTH_SERVICE_BASE_URL="https://central.internal", AUTH_SERVICE_PUBLIC_BASE_URL="https://home.test", SAMPARK_PRODUCT_UNIQUE_ID="samvaad", COGNITO_POST_LOGIN_REDIRECT_URL="https://product.test/", CENTRAL_PROFILE_API_KEY="")
    store = Store()
    monkeypatch.setattr(routes, "settings", config)
    monkeypatch.setattr(routes, "async_redis", SimpleNamespace(client=store))
    return config, store


def request(cookies=""):
    return fastapi.Request({"type":"http","method":"GET","path":"/api/auth/sso/login","headers":[(b"cookie",cookies.encode())],"query_string":b"","session":{}})


@pytest.mark.asyncio
async def test_login_creates_host_only_http_only_cookie_and_keeps_shared_session_untouched(setup):
    req = request()
    res = await routes.sso_login(req, "/practice")
    state = parse_qs(urlsplit(res.headers["location"]).query)["state"][0]
    assert tx.cookie_name(state) in res.headers["set-cookie"]
    assert "HttpOnly" in res.headers["set-cookie"] and "Secure" in res.headers["set-cookie"]
    assert "Domain=" not in res.headers["set-cookie"]
    assert req.session == {}
    assert res.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_redis_outage_is_not_a_permissive_fallback(setup):
    _, store = setup
    store.set = AsyncMock(side_effect=ConnectionError())
    with pytest.raises(fastapi.HTTPException) as exc:
        await routes.sso_login(request(), "/")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_too_many_pending_flows_are_bounded(setup):
    with pytest.raises(fastapi.HTTPException) as exc:
        await routes.sso_login(request("; ".join(f"{tx.COOKIE_PREFIX}{i}=value" for i in range(8))), "/")
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_invalid_callback_does_not_create_transaction(setup):
    config, store = setup
    config.SSO_REDIRECT_URI = "http://public.test/callback"
    with pytest.raises(fastapi.HTTPException):
        await routes.sso_login(request(), "/")
    assert not store.data


@pytest.mark.asyncio
async def test_invalid_state_does_not_exchange_or_touch_user(setup):
    repo = SimpleNamespace(get_user_by_email=AsyncMock())
    response = await routes.sso_callback(request(), code="unused", state="bad", user_repo=repo)
    assert "Invalid%20or%20expired" in response.headers["location"]
    repo.get_user_by_email.assert_not_called()


@pytest.mark.asyncio
async def test_denial_consumes_only_its_transaction_and_clears_cookie(setup):
    _, store = setup
    state, binding = await tx.create(store, "/practice")
    other, other_binding = await tx.create(store, "/reports")
    res = await routes.sso_callback(request(f"{tx.cookie_name(state)}={binding}"), state=state, error="access_denied", requestCode="request-code", user_repo=None)
    assert "access_denied" in res.headers["location"]
    assert "Max-Age=0" in res.headers["set-cookie"]
    assert await tx.consume(store, other, other_binding) == {"destination":"/reports"}


@pytest.mark.asyncio
async def test_success_preserves_destination_without_losing_query(setup, monkeypatch):
    _, store = setup
    state, binding = await tx.create(store, "/reports/2?view=all#score")
    monkeypatch.setattr(routes, "decode_sso_access_token", lambda token: {"sub":"s@example.test","userUniqueId":"u1"})
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, headers={"content-type":"application/json"}, json=lambda: {"isSuccess":True,"data":{"accessToken":"test-access","refreshToken":"test-refresh"}})
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: Client())
    repo = SimpleNamespace(get_user_by_email=AsyncMock(return_value=SimpleNamespace(id=1,is_onboarded=True)),sync_central_identity=AsyncMock())
    res = await routes.sso_callback(request(f"{tx.cookie_name(state)}={binding}"), state=state, code="test-code", user_repo=repo)
    target = urlsplit(res.headers["location"])
    assert target.netloc == "product.test" and target.path == "/"
    assert parse_qs(target.query)["return_to"] == ["/reports/2?view=all#score"]
    assert "Max-Age=0" in res.headers["set-cookie"]
    assert res.headers["cache-control"] == "no-store"

@pytest.mark.asyncio
async def test_real_local_redis_transactions():
    import os
    if os.environ.get("RUN_LOCAL_AUTH_REDIS") != "1":
        pytest.skip("Explicit isolated Redis acceptance only")
    import redis.asyncio as redis
    client = redis.Redis(host="127.0.0.1", port=16379, db=15, socket_connect_timeout=2, socket_timeout=2)
    states = []
    try:
        state, binding = await tx.create(client, "/interview/12")
        states.append(state)
        assert 0 < await client.ttl(tx.PREFIX + state) <= 300
        assert await tx.consume(client, state, "x" * 43) is None
        results = await asyncio.gather(*(tx.consume(client, state, binding) for _ in range(20)))
        assert sum(result is not None for result in results) == 1
        state, binding = await tx.create(client, "/practice")
        states.append(state)
        await client.pexpire(tx.PREFIX + state, 1)
        await asyncio.sleep(.02)
        assert await tx.consume(client, state, binding) is None
    finally:
        for state in states:
            await client.delete(tx.PREFIX + state)
        await client.aclose()

@pytest.mark.asyncio
async def test_v2_challenge_verifier_stays_server_side(setup):
    import base64
    import hashlib
    config, store = setup
    config.SSO_V2_ENABLED = True
    store.get = AsyncMock(side_effect=lambda key: store.data.get(key))
    response = await routes.sso_login(request(), "/reports/4")
    location = response.headers["location"]
    params = parse_qs(urlsplit(location).query)
    assert "/v2/authorize?" in location
    state = params["state"][0]
    payload = json.loads(store.data[tx.PREFIX + state][65:])
    expected = base64.urlsafe_b64encode(hashlib.sha256(payload["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert params["code_challenge"] == [expected]
    assert params["code_challenge_method"] == ["S256"]
    assert payload["verifier"] not in location + response.headers["set-cookie"]
    assert payload["callback"] == config.SSO_REDIRECT_URI


@pytest.mark.asyncio
async def test_v2_requires_transaction_mode(setup):
    config, store = setup
    config.SSO_V2_ENABLED = True
    config.SSO_TRANSACTION_ENABLED = False
    with pytest.raises(fastapi.HTTPException) as exc:
        await routes.sso_login(request(), "/")
    assert exc.value.status_code == 503
    assert not store.data


@pytest.mark.asyncio
@pytest.mark.parametrize("response_body", [None, [], {}, {"isSuccess":False,"errorMessage":"secret internal failure"}, {"isSuccess":True,"data":None}, {"isSuccess":True,"data":{}}, {"isSuccess":True,"data":{"accessToken":[],"refreshToken":"x"}}, {"isSuccess":True,"data":{"accessToken":"x","refreshToken":""}}])
async def test_malformed_exchange_is_safe_error(setup, monkeypatch, response_body):
    _, store = setup
    state, binding = await tx.create(store, "/reports")
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, headers={"content-type":"application/json"}, json=lambda:response_body)
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs:Client())
    res = await routes.sso_callback(request(f"{tx.cookie_name(state)}={binding}"), state=state, code="test", user_repo=None)
    assert "Sign-in%20failed" in res.headers["location"]
    assert "secret" not in res.headers["location"]


@pytest.mark.asyncio
async def test_v2_callback_uses_stored_binding_and_verifier(setup, monkeypatch):
    config, store = setup
    config.SSO_V2_ENABLED = True
    state, binding = await tx.create(store, "/practice", protocol="v2", product="original-product", callback="https://original.test/callback")
    original = json.loads(store.data[tx.PREFIX + state][65:])
    captured = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, json):
            captured.append((url, json))
            return SimpleNamespace(status_code=400, headers={}, json=lambda: {})
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: Client())
    await routes.sso_callback(request(f"{tx.cookie_name(state)}={binding}"), state=state, code="code", user_repo=None)
    assert captured[0][0].endswith("/v2/token")
    assert captured[0][1] == {"code":"code","product":"original-product","redirect_uri":"https://original.test/callback","code_verifier":original["verifier"]}


@pytest.mark.asyncio
async def test_v2_rollback_does_not_downgrade_pending_exchange(setup):
    _, store = setup
    state, binding = await tx.create(store, "/", protocol="v2", product="p", callback="https://p.test/callback")
    res = await routes.sso_callback(request(f"{tx.cookie_name(state)}={binding}"), state=state, code="x", user_repo=None)
    assert "configuration%20changed" in res.headers["location"]


@pytest.mark.asyncio
async def test_v2_activation_requires_old_login_restart(setup):
    config, store = setup
    config.SSO_V2_ENABLED = True
    state, binding = await tx.create(store, "/")
    res = await routes.sso_callback(request(f"{tx.cookie_name(state)}={binding}"), state=state, code="x", user_repo=None)
    assert "restart%20sign-in" in res.headers["location"]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["unknown", "v3"])
async def test_unknown_protocol_not_stored(protocol):
    store = Store()
    with pytest.raises(ValueError):
        await tx.create(store, "/", protocol=protocol)
    assert not store.data


@pytest.mark.asyncio
async def test_v2_missing_binding_not_stored():
    store = Store()
    with pytest.raises(ValueError):
        await tx.create(store, "/", protocol="v2")
    assert not store.data

@pytest.mark.asyncio
async def test_handoff_requires_matching_browser_and_is_one_use():
    from src.services import sso_handoff as handoff
    store = Store()
    ticket, binding = await handoff.issue(store, "access", "refresh", "/practice")
    assert await handoff.redeem(store, ticket, "x"*43) is None
    results = await asyncio.gather(*(handoff.redeem(store, ticket, binding) for _ in range(10)))
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result)["token"] == "access"
    assert store.expiry[handoff.PREFIX + ticket] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "https://evil.test", "null", "https://product.test.evil.test"])
async def test_handoff_rejects_untrusted_origin_without_consumption(setup, origin):
    from src.services import sso_handoff as handoff
    config, store = setup
    config.SSO_V2_ENABLED = True
    ticket, binding = await handoff.issue(store, "access", "refresh", "/practice")
    req = request(f"{handoff.COOKIE}{ticket}={binding}")
    if origin: req.scope["headers"].append((b"origin", origin.encode()))
    res = await routes.complete_sign_in(req, routes.HandoffBody(ticket=ticket))
    assert res.status_code == 403
    assert handoff.PREFIX + ticket in store.data


@pytest.mark.asyncio
async def test_handoff_route_returns_pair_once_without_caching(setup):
    from src.services import sso_handoff as handoff
    config, store = setup
    config.SSO_V2_ENABLED = True
    ticket, binding = await handoff.issue(store, "access", "refresh", "/practice")
    def req():
        r = request(f"{handoff.COOKIE}{ticket}={binding}")
        r.scope["headers"].append((b"origin", b"https://product.test"))
        return r
    res = await routes.complete_sign_in(req(), routes.HandoffBody(ticket=ticket))
    assert res.status_code == 200 and json.loads(res.body)["token"] == "access"
    assert res.headers["cache-control"] == "no-store"
    assert "Max-Age=0" in res.headers["set-cookie"]
    assert (await routes.complete_sign_in(req(), routes.HandoffBody(ticket=ticket))).status_code == 401


@pytest.mark.asyncio
async def test_v2_callback_never_places_tokens_in_redirect(setup, monkeypatch):
    from src.services import sso_handoff as handoff
    config, store = setup
    config.SSO_V2_ENABLED = True
    state, binding = await tx.create(store, "/practice", protocol="v2", product="samvaad", callback=config.SSO_REDIRECT_URI)
    monkeypatch.setattr(routes, "decode_sso_access_token", lambda token:{"sub":"s@example.test","userUniqueId":"u1"})
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs):
            return SimpleNamespace(status_code=200,headers={"content-type":"application/json"},json=lambda:{"isSuccess":True,"data":{"accessToken":"sensitive-access","refreshToken":"sensitive-refresh"}})
    monkeypatch.setattr(routes.httpx,"AsyncClient",lambda **kwargs:Client())
    repo=SimpleNamespace(get_user_by_email=AsyncMock(return_value=SimpleNamespace(id=1,is_onboarded=True)),sync_central_identity=AsyncMock())
    res=await routes.sso_callback(request(f"{tx.cookie_name(state)}={binding}"),state=state,code="code",user_repo=repo)
    location=res.headers["location"]
    assert "sensitive" not in location and "token=" not in location
    assert urlsplit(location).query == "flow=handoff"
    ticket=parse_qs(urlsplit(location).fragment)["handoff"][0]
    assert handoff.PREFIX + ticket in store.data
    cookies=";".join(res.headers.getlist("set-cookie"))
    assert handoff.COOKIE + ticket in cookies and "HttpOnly" in cookies

@pytest.mark.asyncio
async def test_real_local_redis_v2_and_handoff():
    import os
    if os.environ.get("RUN_LOCAL_AUTH_REDIS") != "1":
        pytest.skip("Explicit isolated Redis acceptance only")
    import redis.asyncio as redis
    from src.services import sso_handoff as handoff
    client = redis.Redis(host="127.0.0.1", port=16379, db=15, socket_connect_timeout=2, socket_timeout=2)
    keys=[]
    try:
        state,binding = await tx.create(client,"/practice",protocol="v2",product="test",callback="https://test.test/callback")
        keys.append(tx.PREFIX+state)
        challenge=await tx.challenge(client,state,binding)
        assert len(challenge)==43
        record=await tx.consume(client,state,binding)
        assert record["protocol"]=="v2" and len(record["verifier"])==64
        ticket,cookie=await handoff.issue(client,"synthetic-access","synthetic-refresh","/practice")
        keys.append(handoff.PREFIX+ticket)
        assert await handoff.redeem(client,ticket,"x"*43) is None
        values=await asyncio.gather(*(handoff.redeem(client,ticket,cookie) for _ in range(20)))
        assert sum(v is not None for v in values)==1
        ticket,cookie=await handoff.issue(client,"synthetic-access","synthetic-refresh","/practice")
        keys.append(handoff.PREFIX+ticket)
        await client.pexpire(handoff.PREFIX+ticket,1)
        await asyncio.sleep(.02)
        assert await handoff.redeem(client,ticket,cookie) is None
    finally:
        for key in keys: await client.delete(key)
        await client.aclose()


@pytest.mark.asyncio
async def test_error_callback_has_visible_route_marker(setup):
    res = await routes.sso_callback(request(), state="invalid")
    assert parse_qs(urlsplit(res.headers["location"]).query)["flow"] == ["callback"]
    assert "error=" in urlsplit(res.headers["location"]).fragment
