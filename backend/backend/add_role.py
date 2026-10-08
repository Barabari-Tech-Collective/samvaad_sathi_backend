import re
with open('src/api/routes/users.py', 'r', encoding='utf-8') as f:
    content = f.read()

def replacer(match):
    return match.group(0) + '\n                                    role="ADMIN" if is_admin_user(current_user) else "USER",'

if 'role=' not in content:
    content = re.sub(r'is_admin=is_admin_user\(current_user\),', replacer, content)
    
    # Also update login/register which doesn't have is_admin=False, actually wait, they don't have is_admin=False either!
    content = content.replace('is_onboarded=user_is_onboarded,', 'is_onboarded=user_is_onboarded,\n                                    is_admin=False,\n                                    role="USER",')
    
    with open('src/api/routes/users.py', 'w', encoding='utf-8') as f:
        f.write(content)
