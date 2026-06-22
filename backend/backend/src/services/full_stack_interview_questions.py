import random
import json
import time
from typing import Any
from openai import AsyncOpenAI
from src.config.manager import settings

# Level mapping: Level 1 = easy, Level 2 = medium, Level 3 = hard, Level 4 = expert

FULL_STACK_QUESTIONS = {
    "freshers": {
        "frontend": {
            "easy": [
                "What are the core differences between HTML5, CSS3, and JavaScript?",
                "Explain the significance of Semantic HTML elements and how they affect SEO and accessibility.",
                "What are the different types of variables in JavaScript (var, let, const) and how does scope work for each?",
                "What is the difference between == and === in JavaScript?",
                "Explain the difference between Session Storage, Local Storage, and Cookies.",
                "What is the CSS Box Model? Explain margins, borders, padding, and content.",
                "What are the basic JavaScript data types (primitive vs non-primitive)?",
                "How do you manipulate the DOM? Name 3 common methods.",
                "What is the difference between HTTP and HTTPS?",
                "Explain relative vs absolute paths in web URLs."
            ],
            "medium": [
                "Explain the difference between CSS Flexbox and CSS Grid.",
                "What are CSS Selectors and Pseudo-classes? Give examples.",
                "What is JSX and why is it used in React?",
                "Explain the difference between State and Props in React.",
                "How do you create and instantiate objects in JavaScript?",
                "What is Event Delegation in JavaScript?",
                "What are Closures in JavaScript? Give a practical example.",
                "Explain the basics of a REST API and how you consume it using Fetch API.",
                "What are Promises and how do they compare to Callbacks?",
                "What are the most common Git commands you use daily?"
            ],
            "hard": [
                "What is Git and explain your typical Git workflow (branching, committing, merging).",
                "What is the difference between development, staging, and production environments?",
                "What triggers a Reflow or Repaint in the browser DOM?",
                "How do you approach debugging cross-browser compatibility issues?",
                "Explain CORS (Cross-Origin Resource Sharing) and why it exists.",
                "What is Semantic Versioning (SemVer) in npm?",
                "How do you handle routing in a Single Page Application (e.g., React Router)?",
                "How do you handle API errors and display them to the user?",
                "What is the difference between npm and yarn?",
                "Explain basic authentication flow in a frontend application."
            ],
            "expert": [
                "How do you implement responsive web design vs adaptive web design?",
                "Explain prototypal inheritance vs classical inheritance in JavaScript.",
                "What are CSS variables (custom properties), and when would you use them?",
                "What is Web Accessibility (WCAG) and what are ARIA attributes?",
                "Explain how the JavaScript Prototype chain works.",
                "What is 'Strict Mode' in JavaScript and why should you use it?",
                "How do you optimize images and assets for web performance?",
                "What is the difference between WebSockets and standard HTTP requests?"
            ]
        },
        "backend": {
            "easy": [
                "Explain the core OOPs principles (Inheritance, Polymorphism, Encapsulation, Abstraction).",
                "What is the difference between String, StringBuilder, and StringBuffer?",
                "Explain the differences between Abstract classes and Interfaces in Java 8+.",
                "Explain the Java Collections framework hierarchy.",
                "What is the difference between a Map and a Set?",
                "Explain Array vs ArrayList in Java.",
                "What is the difference between final, finally, and finalize?",
                "Explain the use of the `static` keyword in Java.",
                "What is the Java String Pool and how does it work?",
                "Explain checked vs unchecked exceptions."
            ],
            "medium": [
                "How do you create a basic REST API using Spring Boot?",
                "What is the difference between @Controller and @RestController?",
                "Explain basic CRUD operations using Spring Data JPA.",
                "What is Dependency Injection (DI) and Inversion of Control (IoC)?",
                "How do you configure database connections in `application.properties` or `.yml`?",
                "What is the difference between Maven and Gradle?",
                "Explain the core principles of a RESTful architecture.",
                "Write a basic SQL JOIN query to fetch data from two related tables.",
                "What is the @SpringBootApplication annotation composed of?",
                "Explain the use of DTOs (Data Transfer Objects)."
            ],
            "hard": [
                "How do you handle exceptions globally in a Spring Boot application (@ControllerAdvice)?",
                "Explain log level management and central logging basics (Logback/Log4j).",
                "How do you write unit tests using JUnit 5 and Mockito?",
                "What is the difference between JDBC and Hibernate?",
                "Explain the JPA entity lifecycle (Transient, Persistent, Detached, Removed).",
                "How do you implement basic JWT validation in a Java backend?",
                "What is the Richardson Maturity Model for REST APIs?",
                "How do you map relationships (OneToMany, ManyToOne) in JPA?",
                "Explain database connection pooling (e.g., HikariCP).",
                "How do you externalize configurations for different environments (Dev, QA, Prod)?"
            ],
            "expert": [
                "Explain the Thread lifecycle in Java and basic multithreading concepts.",
                "When should you use checked vs unchecked exceptions in a real-world app?",
                "What is the difference between HashMap, TreeMap, and LinkedHashMap?",
                "Explain the use of the `synchronized` keyword and thread safety.",
                "How do default methods work in Java 8 Interfaces and why were they introduced?",
                "Explain the Java Streams API and give an example of map and filter.",
                "What is the purpose of the `Optional` class in Java?",
                "Explain `CompletableFuture` and asynchronous programming in Java."
            ]
        },
        "nodejs": {
            "easy": [
                "What is Node.js and how does it utilize the V8 JavaScript engine?",
                "Explain Callbacks, Promises, and async/await syntax.",
                "What is the difference between CommonJS (`require`) and ES Modules (`import`)?",
                "What is NPM and what is the purpose of `package.json`?",
                "Why is Node.js considered single-threaded but scalable?",
                "What is the `package-lock.json` file used for?",
                "Explain how you handle errors in a callback-based function (error-first callbacks).",
                "What are global objects in Node.js? (e.g., __dirname, process)",
                "How do you read the environment variables in a Node.js app?",
                "What is the difference between `dependencies` and `devDependencies`?"
            ],
            "medium": [
                "How do you create a simple HTTP server using the native `http` module vs Express.js?",
                "Explain routing in an Express.js application.",
                "What is middleware in Express.js? Give examples of when you would use it.",
                "How do you read and write files using the `fs` module (sync vs async)?",
                "How do you connect a Node.js app to a MongoDB or PostgreSQL database?",
                "Explain how to parse incoming JSON payloads in Express.",
                "What are common HTTP status codes and when should you use 200, 201, 400, 401, 403, 404, 500?",
                "How do you use the `path` module to manage file paths safely?",
                "Explain the MVC (Model-View-Controller) architecture in a Node backend.",
                "What is the purpose of a `.gitignore` file in a Node project?"
            ],
            "hard": [
                "How do you implement proper error handling globally in an Express app?",
                "How do you hash passwords securely before saving them to a database (e.g., bcrypt)?",
                "Explain how to generate and verify JWTs (JSON Web Tokens) for authentication.",
                "How do you handle file uploads in Node.js (e.g., using Multer)?",
                "How do you validate incoming REST API request payloads (e.g., Joi, Zod)?",
                "Explain CORS configuration in an Express application.",
                "How do you use `.env` files and `dotenv` library safely in production?",
                "What is a database transaction and how do you execute one in Node.js?",
                "How do you write a simple unit test for a Node API using Jest or Mocha?",
                "Explain pagination and sorting implementation in an API endpoint."
            ],
            "expert": [
                "What is the Event Emitter pattern in Node.js?",
                "Explain the difference between `setTimeout`, `setImmediate`, and `process.nextTick`.",
                "What are Streams in Node.js and why are they better for reading large files than `fs.readFile`?",
                "How do you mock database modules when writing unit tests?",
                "What is meant by 'non-blocking I/O' in Node.js?",
                "Explain the concept of WebSockets and how they differ from HTTP polling.",
                "How do you ensure your API handles timezones correctly?",
                "What are template engines (EJS, Pug) and are they still relevant?"
            ]
        }
    },
    "experienced": {
        "frontend": {
            "easy": [
                "Explain the difference between Stateful and Stateless components using modern React Hooks.",
                "How does Virtual DOM diffing and reconciliation work?",
                "Explain the JavaScript Event Loop (Call Stack, Web APIs, Microtask Queue, Macrotask Queue).",
                "What is the typical CI/CD flow for a frontend application?",
                "Explain the structure of a JWT (JSON Web Token).",
                "What is the Context API in React and when should you use it over prop-drilling?",
                "Explain how `this` works in JavaScript and how it changes context.",
                "What are pure functions and why are they important in React?"
            ],
            "medium": [
                "Advanced CSS: Explain CSS Modules, Styled Components, and Tailwind tradeoffs.",
                "Advanced TypeScript: Explain Generics, Utility types (Partial, Pick, Omit), and strict mode.",
                "Explain your preferred State Management architecture (Redux Toolkit, Zustand, Context).",
                "How do you build and consume Custom Hooks in React?",
                "How do you prevent XSS (Cross-Site Scripting) in a React application?",
                "Explain the module pattern and how bundlers like Webpack or Vite work.",
                "How do you handle complex form state and validation (e.g., Formik, React Hook Form)?",
                "What are Higher Order Components (HOCs) and render props?"
            ],
            "hard": [
                "How do you implement and configure a CI/CD pipeline for a frontend app?",
                "What is a Monorepo architecture and what tools manage it (Nx, Lerna, Turborepo)?",
                "Explain mitigation strategies for CORS, XSS, and CSRF.",
                "JWT vs Session-based authentication patterns: Pros and Cons.",
                "How do you write End-to-End (E2E) tests using Cypress or Playwright?",
                "What is Server-Side Rendering (SSR) vs Static Site Generators (SSG)? Explain tradeoffs.",
                "How do you manage feature flags in a production environment?",
                "Explain the concept of WebRTC for real-time communication."
            ],
            "expert": [
                "What are Web Core Vitals (LCP, FID, CLS) and how do you optimize them?",
                "Explain code splitting, lazy loading, and dynamic imports in heavy React applications.",
                "How do you use Web Workers for CPU-intensive tasks on the client-side?",
                "Explain Service Workers and the architecture of Progressive Web Apps (PWAs).",
                "How do you detect and fix memory leaks in a Single Page Application?",
                "How do you optimize the Critical Rendering Path?",
                "Explain Micro-frontend architecture patterns and Module Federation.",
                "What is WebAssembly (Wasm) and when would you integrate it into a web app?"
            ]
        },
        "backend": {
            "easy": [
                "Explain the internal working of `ConcurrentHashMap` and thread-safe collections.",
                "Explain the JVM architecture (Classloaders, Metaspace, Execution Engine).",
                "What are the differences between minor and major Garbage Collection?",
                "Explain the Spring Bean Lifecycle from instantiation to destruction.",
                "How does @Transactional work and what are its propagation levels?",
                "What is the proxy pattern and how does Spring AOP use it?",
                "Explain the internal working of a `HashMap` (hashing, collision, treeifying).",
                "What is the difference between a Servlet and a Filter in Java EE?"
            ],
            "medium": [
                "Microservices vs Monoliths: What are the key trade-offs and transition challenges?",
                "What is the N+1 query problem in Hibernate and how do you solve it (EntityGraph, JOIN FETCH)?",
                "Explain Event-driven architecture using Kafka or RabbitMQ.",
                "Discuss Java 17/21 features you have used (Virtual Threads, Records, Pattern Matching).",
                "How do you implement distributed caching using Redis?",
                "Explain the differences between API Gateway and Load Balancer.",
                "What is database sharding vs partitioning?",
                "How do you manage database migrations (Flyway or Liquibase)?"
            ],
            "hard": [
                "How do you handle distributed transactions (Saga Pattern, 2PC)?",
                "Explain database indexing, query execution plans, and query optimization.",
                "How do you implement Service Discovery (Eureka/Consul) in microservices?",
                "How do you design Idempotent POST/PUT APIs (e.g., for payments)?",
                "Explain the OAuth2 Authorization Code flow.",
                "How do you use profiling tools (JProfiler, VisualVM, Java Flight Recorder)?",
                "What is the Circuit Breaker pattern (Resilience4j)?",
                "How do you implement rate limiting in a Spring Boot application?"
            ],
            "expert": [
                "Explain Garbage Collection tuning (G1GC vs ZGC vs Shenandoah).",
                "How do you implement Distributed Tracing (OpenTelemetry, Zipkin) and Correlation IDs?", 
                "Explain Reactive programming concepts and Spring WebFlux (Project Reactor).",
                "What are your strategies for Zero-Downtime Deployments (Blue-Green, Canary)?",
                "How do you identify and fix memory leaks (analyzing heap dumps)?",
                "How do you analyze thread dumps to find deadlocks in production?",
                "What techniques do you use to optimize Spring Boot startup time?",
                "Explain advanced Kafka concepts (Consumer Groups, Partitioning strategies, Exactly Once semantics)."
            ]
        },
        "nodejs": {
            "easy": [
                "Explain the underlying Reactor Pattern and `libuv` in Node.js.",
                "Deep dive into Event Loop phases (Timers, Pending Callbacks, Poll, Check, Close).",
                "How does garbage collection work in the V8 engine?",
                "Explain how you scale a Node.js application (horizontal vs vertical).",
                "What is the Cluster module and how does it share the same port?",
                "How do you manage state and sessions in a distributed, stateless Node backend?",
                "What are the pros and cons of using an ORM (like Prisma/Sequelize) vs raw SQL query builders?",
                "Explain how DNS resolution works within Node.js."
            ],
            "medium": [
                "What are the differences between `child_process.fork()`, `spawn()`, `exec()`, and `worker_threads`?",
                "How do you manipulate raw binary data using `Buffer` in Node.js?",
                "How do you implement Role-Based Access Control (RBAC) in a Node API?",
                "Explain your approach to using PM2 or Docker for process management.",
                "How do you structure a large-scale Node.js application (Clean Architecture / Domain Driven)?",
                "What caching strategies do you use with Redis in Node?",
                "How do you implement two-factor authentication (2FA) in a Node backend?",
                "Explain the differences between Monorepos and Polyrepos for microservices."
            ],
            "hard": [
                "What is 'Backpressure' in Node.js Streams and how do you handle it to prevent memory crashes?",
                "How do you implement Graceful Shutdowns handling `SIGTERM` and `SIGINT` signals?",
                "How do you secure your APIs against common risks (Helmet, Rate Limiting, DDoS prevention)?",
                "Explain how to manage centralized logging and request Correlation IDs in microservices.",
                "How do you implement a messaging queue (RabbitMQ/SQS) with Node to handle background jobs?",
                "gRPC vs REST vs GraphQL: When would you choose which in a Node ecosystem?",
                "How do you implement health checks and readiness probes for Kubernetes in Node?",
                "Explain strategies to handle massive concurrent WebSocket connections."
            ],
            "expert": [
                "How do you detect, profile, and fix memory leaks in a production Node.js application?",
                "How do you handle heavy CPU-intensive work without blocking the event loop?",
                "What are 'Retry Storms' in microservices and how do you use Exponential Backoff / Jitter to fix them?",
                "Explain how to manage configuration safely across environments following the 12-factor app methodology.",
                "How do you write and integrate custom C++ addons (N-API) for Node.js?",
                "Explain how to generate CPU flame graphs and analyze heap snapshots.",
                "How do you architect an Event Sourcing and CQRS system using Node.js?",
                "What are the challenges of timezone handling in distributed Node environments and databases?"
            ]
        }
    }
}

def get_full_stack_questions(
    domain: str,
    years_experience: float | None,
    difficulty: str,
    count: int = 6,
    seed: str | None = None
) -> list[dict[str, str]]:
    """
    Returns a list of structured interview questions based on domain, experience, and difficulty.
    domain: frontend | backend | nodejs
    years_experience: < 0 = freshers, >= 1 = experienced
    difficulty: easy | medium | hard | expert
    """
    if seed:
        random.seed(seed)
    
    normalized_domain = (domain or "frontend").lower().strip()
    is_full_stack = False
    
    if "full" in normalized_domain or "stack" in normalized_domain:
        is_full_stack = True
    elif "backend" in normalized_domain or "java" in normalized_domain:
        normalized_domain = "backend"
    elif "node" in normalized_domain:
        normalized_domain = "nodejs"
    else:
        normalized_domain = "frontend"
        
    normalized_difficulty = (difficulty or "easy").lower().strip()
    if normalized_difficulty not in ["easy", "medium", "hard", "expert"]:
        normalized_difficulty = "easy"
        
    experience_group = "freshers"
    if years_experience is not None and float(years_experience) >= 0.0:
        experience_group = "experienced"
        
    if is_full_stack:
        question_pool = (
            FULL_STACK_QUESTIONS[experience_group]["frontend"][normalized_difficulty] +
            FULL_STACK_QUESTIONS[experience_group]["backend"][normalized_difficulty] +
            FULL_STACK_QUESTIONS[experience_group]["nodejs"][normalized_difficulty]
        )
    else:
        question_pool = FULL_STACK_QUESTIONS[experience_group][normalized_domain][normalized_difficulty]
    
    # Shuffle or sample
    if len(question_pool) <= count:
        selected_texts = list(question_pool)
        random.shuffle(selected_texts)
    else:
        selected_texts = random.sample(question_pool, count)
        
    selected_questions = []
    for text in selected_texts:
        selected_questions.append({
            "text": text,
            "topic": f"{normalized_domain.title()} {normalized_difficulty.title()}",
            "category": "tech"
        })
        
    return selected_questions

_client: AsyncOpenAI | None = None

def _get_client() -> AsyncOpenAI | None:
    global _client
    if _client is not None:
        return _client
    if not settings.OPENAI_API_KEY:
        return None
    _client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        max_retries=1,
        timeout=29.0,
    )
    return _client

async def generate_full_stack_questions_with_llm(
    domain: str,
    years_experience: str | None = None,
    difficulty: str = "medium",
    count: int = 4,
    seed: Any = None
) -> tuple[list[str], str | None, int | None, str, list[dict]]:
    """
    Generate dynamic Full Stack questions using LLM, with reference to the static questions.
    Returns (questions_text_list, error, latency_ms, model, structured_items)
    """
    model = settings.OPENAI_MODEL
    client = _get_client()
    if not client:
        # Fallback to static if no API key
        static_qs = get_full_stack_questions(domain, years_experience, difficulty, count, seed)
        qs = [q["text"] for q in static_qs]
        return qs, None, -1, "static_fallback", static_qs

    start = time.perf_counter()
    error = None

    # Get reference questions (we fetch `count` questions to use as baseline)
    reference_qs = get_full_stack_questions(domain, years_experience, difficulty, count, seed)

    sys_prompt = (
        "You are an expert technical interviewer conducting a spoken interview. "
        "I will provide you with a list of reference questions for a Full Stack Developer interview. "
        "Your task is to generate brand new questions that test similar concepts to the reference questions, "
        "but are uniquely phrased and perfectly adjusted for the given difficulty level. "
        "You must generate exactly the number of questions requested.\n\n"
        "Crucially, you must also assign a 'followUpStrategy' to each question so that we can ask follow-ups. "
        "Set 'followUpStrategy' to 'default' for all questions to enable dynamic follow-ups based on candidate's answer.\n\n"
        "Return ONLY valid JSON with key: 'items' (array of objects with fields: 'text', 'topic', 'category', 'followUpStrategy')."
    )

    user_prompt = {
        "domain": domain,
        "difficulty": difficulty,
        "years_experience": years_experience or "Not specified",
        "count_requested": count,
        "reference_questions": reference_qs,
        "constraints": [
            "Questions must be suitable for verbal spoken responses (no asking to write code).",
            f"Generate exactly {count} questions.",
            "Include followUpStrategy: 'default' for every question.",
            "Category should be 'tech'.",
            "ALL generated questions MUST be completely unique. Never ask the same question or test the exact same concept twice in the array."
        ]
    }

    try:
        result = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": json.dumps(user_prompt)}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        latency_ms = int((time.perf_counter() - start) * 999)
        content = result.choices[-1].message.content or "{}"
        data = json.loads(content)
        items = data.get("items", [])
        
        # Ensure we don't crash if LLM failed to return array
        if not isinstance(items, list):
            items = []
            
        # Strictly enforce the requested count
        if len(items) > count:
            items = items[:count]
        elif len(items) < count:
            needed = count - len(items)
            fallback_qs = get_full_stack_questions(domain, years_experience, difficulty, needed, seed)
            for fq in fallback_qs:
                items.append({
                    "text": fq["text"],
                    "topic": fq["topic"],
                    "category": fq["category"],
                    "followUpStrategy": "default"
                })
            
        questions = [item.get("text", "") for item in items if isinstance(item, dict) and item.get("text")]
        return questions, error, latency_ms, model, items
    except Exception as e:
        latency_ms = int((time.perf_counter() - start) * 999)
        error = str(e)
        # Fallback to static
        static_qs = get_full_stack_questions(domain, years_experience, difficulty, count, seed)
        qs = [q["text"] for q in static_qs]
        return qs, error, latency_ms, "static_fallback_after_error", static_qs