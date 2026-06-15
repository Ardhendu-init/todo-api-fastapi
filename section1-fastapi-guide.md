# Section 1 — FastAPI & Python Backend: Build It to Own It

> **Goal:** Understand FastAPI deeply enough to answer Q1–Q4 from experience, not documentation.
> Every concept here is demonstrated through a real mini-app: a **Leads API**.
> Ask me about any line in this document and I can explain it.

---

## What You Will Build

A production-style REST API that:
- Accepts a JSON payload, validates it with Pydantic v2, and writes it to MongoDB
- Handles duplicate emails with a 409 response
- Requires a JWT token on protected routes
- Runs async correctly

This maps 1:1 to Q1–Q4.

---

## Task Plan (Execute in Order)

| # | Task | Covers |
|---|------|--------|
| 1 | Set up environment and project structure | Foundation |
| 2 | Define your Pydantic v2 model | Q1 — validation |
| 3 | Connect to MongoDB with Motor (async driver) | Q1 — database |
| 4 | Write the POST /leads endpoint | Q1 — full answer |
| 5 | Understand async def vs def by breaking things | Q2 — concept |
| 6 | Trigger and debug a 422 error intentionally | Q3 — debugging |
| 7 | Write a JWT auth dependency | Q4 — full answer |
| 8 | Protect a route with the dependency | Q4 — injection |

---

## Task 1 — Set Up the Environment

### What you're doing
Creating an isolated Python environment and installing the exact packages this stack uses.

### Steps

```bash
# Create project folder
mkdir leads-api && cd leads-api

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # On Windows: venv\Scripts\activate

# Install dependencies
pip install fastapi "uvicorn[standard]" motor pymongo pydantic[email] python-jose[cryptography] passlib

# Freeze so the repo is reproducible
pip freeze > requirements.txt
```

### Project structure you're building toward

```
leads-api/
├── main.py           # FastAPI app instance and route registration
├── models.py         # Pydantic models (input/output shapes)
├── database.py       # MongoDB connection using Motor
├── auth.py           # JWT dependency
├── requirements.txt
└── .env              # MONGO_URI, SECRET_KEY (never commit this)
```

### Why this structure?
Each file has one responsibility. `main.py` only wires things together — it does not contain business logic. This is the real-world pattern; interviewers notice when code is dumped into a single file.

### Resources
- [FastAPI official quickstart](https://fastapi.tiangolo.com/tutorial/first-steps/) — read "First Steps" and "Path Parameters" only
- [Motor (async MongoDB driver) docs](https://motor.readthedocs.io/en/stable/)

---

## Task 2 — Define Your Pydantic v2 Model

### What you're doing
Writing the schema that validates incoming JSON before it ever touches your database. This is the core of Q1.

### Concept: What is Pydantic?
Pydantic is a data validation library. When FastAPI receives a POST body, it passes the raw JSON to your Pydantic model. If the data doesn't match the model, FastAPI automatically returns a **422 Unprocessable Entity** — before your function even runs. You never write `if email is None` guards; Pydantic handles it.

**Pydantic v1 vs v2 — the key difference:**
- v1 used `@validator` decorator
- v2 uses `@field_validator` (class method, different signature)
- The questionnaire explicitly says "Pydantic v2 validators" — this distinction matters

### Create `models.py`

```python
from pydantic import BaseModel, EmailStr, field_validator, ConfigDict
from datetime import datetime, timezone


class LeadCreate(BaseModel):
    """
    Input model — what the API consumer sends.
    EmailStr automatically validates email format.
    score must be 0-100; we enforce this with a field_validator.
    created_at is NOT here — the server sets it, not the client.
    """
    name: str
    email: EmailStr          # Pydantic validates format: requires @ and a domain
    score: int

    @field_validator("score")
    @classmethod                # Required in Pydantic v2 — validators are class methods
    def score_must_be_in_range(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError("score must be between 0 and 100")
        return v


class LeadInDB(LeadCreate):
    """
    Output model — what we return after inserting.
    Adds created_at and the MongoDB-generated id.
    """
    id: str
    created_at: datetime

    model_config = ConfigDict(populate_by_name=True)
```

### Line-by-line explanation

**`EmailStr`** — This is a special Pydantic type from `pydantic[email]`. It rejects strings like `"notanemail"` or `"foo@"` before your route handler sees them.

**`@field_validator("score")`** — Pydantic v2 syntax. The string `"score"` tells Pydantic which field to validate. If you write `@validator("score")` (v1 style), it will silently not run in v2.

**`@classmethod`** — Mandatory in Pydantic v2. If you forget it, you get a `PydanticUserError` at startup.

**`raise ValueError(...)`** — Pydantic catches this and formats it into the 422 response body automatically.

**`LeadInDB(LeadCreate)`** — Inheritance. `LeadInDB` has all fields of `LeadCreate` plus `id` and `created_at`. This avoids repetition and shows clear separation between what comes IN and what goes OUT.

### Resources
- [Pydantic v2 field_validator docs](https://docs.pydantic.dev/latest/concepts/validators/#field-validators)
- [Pydantic v2 migration guide](https://docs.pydantic.dev/latest/migration/) — read the "Validators" section

---

## Task 3 — Connect to MongoDB

### What you're doing
Writing the async MongoDB connection. This is the reason your route handlers must be `async def`.

### Concept: Why Motor instead of PyMongo?
PyMongo is synchronous — it blocks the thread while waiting for a database response. FastAPI runs on an async event loop (ASGI). If you use a blocking driver inside an `async def` route, you block the entire event loop and every other request waits. Motor is PyMongo's async wrapper — it awaits the database instead of blocking.

### Create `database.py`

```python
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()   # Reads .env file into environment variables

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "leads_db"

client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]

leads_collection = db["leads"]
```

### Create `.env`

```
MONGO_URI=mongodb://localhost:27017
SECRET_KEY=your-secret-key-change-this
```

### Why `AsyncIOMotorClient`?
The `AsyncIO` prefix signals that this client is built for Python's `asyncio` event loop. Every method on this client (`find_one`, `insert_one`, etc.) returns a coroutine — you must `await` it.

### Resources
- [Motor tutorial](https://motor.readthedocs.io/en/stable/tutorial-asyncio.html) — only the "Creating a Client" and "Inserting a Document" sections

---

## Task 4 — Write the POST /leads Endpoint (Q1 Full Answer)

### What you're doing
This is the full answer to Q1. Read each comment as if explaining it in an interview.

### Create `main.py`

```python
from fastapi import FastAPI, HTTPException, status
from datetime import datetime, timezone

from models import LeadCreate, LeadInDB
from database import leads_collection

app = FastAPI()


@app.post("/leads", response_model=LeadInDB, status_code=status.HTTP_201_CREATED)
async def create_lead(lead: LeadCreate):
    """
    POST /leads
    - Validates input with Pydantic (happens before this function runs)
    - Checks for duplicate email → 409
    - Inserts into MongoDB
    - Returns the inserted document with its ID → 201
    """

    # Step 1: Check for duplicate email
    existing = await leads_collection.find_one({"email": lead.email})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A lead with email '{lead.email}' already exists."
        )

    # Step 2: Build the document to insert
    # model_dump() converts the Pydantic model to a plain Python dict
    lead_dict = lead.model_dump()
    lead_dict["created_at"] = datetime.now(timezone.utc)   # Server sets this, never trust client

    # Step 3: Insert into MongoDB
    result = await leads_collection.insert_one(lead_dict)

    # Step 4: Build and return the response
    # MongoDB's _id is a BSON ObjectId — not JSON-serializable — so we convert to str
    return LeadInDB(
        id=str(result.inserted_id),
        **lead_dict
    )
```

### Line-by-line explanation

**`@app.post("/leads", response_model=LeadInDB, status_code=status.HTTP_201_CREATED)`**
- `response_model=LeadInDB` — FastAPI uses this to filter the response. Even if your dict has extra fields, only fields in `LeadInDB` are returned. This prevents accidental data leakage.
- `status_code=201` — By default FastAPI returns 200. You override it here. Q1 explicitly asks for 201.

**`async def create_lead(lead: LeadCreate)`**
- `lead: LeadCreate` — FastAPI sees this type hint and knows to parse the request body as a `LeadCreate`. If parsing fails, it returns 422 automatically.

**`await leads_collection.find_one({"email": lead.email})`**
- `await` — this is a non-blocking database call. The event loop can handle other requests while waiting.
- `find_one({"email": ...})` — MongoDB query. Returns `None` if no match.

**`raise HTTPException(status_code=409, detail="...")`**
- FastAPI converts this into a JSON error response. The client receives `{"detail": "A lead with email ... already exists."}` with HTTP status 409.

**`lead.model_dump()`**
- Pydantic v2 method (v1 used `.dict()`). Returns `{"name": "...", "email": "...", "score": 50}`.

**`datetime.now(timezone.utc)`**
- Always use `timezone.utc` for timestamps in APIs. Timezone-naive datetimes are a common source of bugs in multi-region deployments.

**`str(result.inserted_id)`**
- `inserted_id` is a `bson.ObjectId` object. If you return it directly, JSON serialization fails with a `TypeError`. Always convert to string.

### Run and test it

```bash
# Start MongoDB (if running locally)
mongod --dbpath /tmp/mongodb-data

# Start the API
uvicorn main:app --reload

# Test in another terminal
curl -X POST http://localhost:8000/leads \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice", "email": "alice@example.com", "score": 85}'

# Expected: 201 with {"id": "...", "name": "Alice", ...}

# Send again — same email
curl -X POST http://localhost:8000/leads \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice", "email": "alice@example.com", "score": 85}'

# Expected: 409 with {"detail": "A lead with email 'alice@example.com' already exists."}
```

---

## Task 5 — Understand async def vs def (Q2 Full Answer)

### The mental model

FastAPI runs on an **async event loop** (via `uvicorn` + `starlette`). Think of the event loop as a single chef in a kitchen. The chef can:
- Start boiling water (database call), then
- While waiting, start chopping vegetables (handle another request)

That is async. If the chef stands and stares at the pot until it boils, that's blocking — every other task waits.

### `async def` — use when you are doing I/O

```python
@app.get("/leads/{id}")
async def get_lead(id: str):
    # await suspends THIS coroutine and gives the event loop back
    # Other requests can be handled while we wait for MongoDB
    lead = await leads_collection.find_one({"_id": ObjectId(id)})
    return lead
```

Use `async def` when your function contains `await` — i.e., you're calling an async library (Motor, httpx async client, aiofiles, Redis async client).

### `def` — use for CPU-bound or sync-only logic

```python
@app.get("/health")
def health_check():
    # No I/O. Just returns a dict. 
    # FastAPI runs this in a thread pool so it doesn't block the event loop.
    return {"status": "ok"}
```

FastAPI is smart: if you define a route with plain `def`, it automatically runs it in a **thread pool executor** — so it doesn't block the event loop either. But if you use `async def` and then call a **synchronous** blocking function inside it (like regular `pymongo`), you DO block the event loop because you've told FastAPI "I'm handling concurrency myself" and then stalled.

### The critical mistake to avoid

```python
# WRONG: async def with a synchronous (blocking) database driver
@app.post("/leads")
async def create_lead_wrong(lead: LeadCreate):
    import pymongo                          # synchronous driver
    client = pymongo.MongoClient(MONGO_URI)
    # This blocks the event loop — every other request waits here
    result = client["leads_db"]["leads"].insert_one(lead.model_dump())
    return {"id": str(result.inserted_id)}
```

This is why Q2 asks for a real project example — they want you to know this pitfall, not just recite "async is faster."

### Your real-project answer template

> "In my Leads API, all database routes use `async def` because Motor is an async driver — every `await leads_collection.find_one(...)` call yields control back to the event loop, so the server handles concurrent requests without blocking. I use plain `def` only for routes that do pure computation with no I/O, like health checks or config endpoints. I made the mistake once of using `async def` with a synchronous Redis client — the event loop stalled under load, which I caught with a simple `locust` load test."

---

## Task 6 — Trigger and Debug a 422 (Q3 Full Answer)

### What is a 422?
**422 Unprocessable Entity** means the server understood the request format (valid HTTP, valid JSON) but the data inside failed validation. Pydantic validation failed before your route handler ran.

### Step 1: Trigger it intentionally

```bash
# Missing required field
curl -X POST http://localhost:8000/leads \
  -H "Content-Type: application/json" \
  -d '{"name": "Bob", "score": 50}'

# Response: 422 — 'email' field is required
```

```bash
# Wrong type
curl -X POST http://localhost:8000/leads \
  -H "Content-Type: application/json" \
  -d '{"name": "Bob", "email": "bob@example.com", "score": "not-a-number"}'

# Response: 422 — score must be an integer
```

### Step 2: Read the 422 response body

The response body tells you exactly what failed:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "email"],
      "msg": "Field required",
      "input": {"name": "Bob", "score": 50},
      "url": "https://errors.pydantic.dev/..."
    }
  ]
}
```

- `"loc"` — where in the payload the error is. `["body", "email"]` means the `email` field in the request body.
- `"msg"` — human-readable reason.
- `"type"` — machine-readable error code.

### Step 3: Debugging toolkit

**Tool 1 — FastAPI's built-in `/docs` (Swagger UI)**
Go to `http://localhost:8000/docs`. This shows the exact schema your endpoint expects. Compare it against what you're sending.

**Tool 2 — Print the raw request**

```python
from fastapi import Request

@app.post("/leads/debug")
async def debug_lead(request: Request):
    body = await request.json()
    print(body)           # See exactly what arrived
    return body
```

**Tool 3 — Read the validation error directly**

```python
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print("Validation failed:", exc.errors())   # Full error detail in your terminal
    return JSONResponse(status_code=422, content={"detail": exc.errors()})
```

### The 3 most common causes of 422

**Cause 1: Field name mismatch**
You send `"Email"` (capital E) but the model expects `"email"`. JSON is case-sensitive. Fix: Check `loc` in the error response.

**Cause 2: Wrong Content-Type header**
You forget `-H "Content-Type: application/json"` in curl. FastAPI receives the body as form data, not JSON. Fix: Always set `Content-Type: application/json` for JSON endpoints.

**Cause 3: Type coercion failed silently (or didn't)**
You send `"score": "85"` (a string) expecting Pydantic to coerce it to int. In Pydantic v2 with strict mode, it won't. In lenient mode (default), it will. If your model has strict validators, a string "85" triggers a 422. Fix: Send the correct type, or understand your model's coercion settings.

---

## Task 7 — Write the JWT Dependency (Q4 Full Answer)

### What you're doing
Writing a FastAPI **dependency** that extracts and validates a JWT from the `Authorization` header. If the token is missing or invalid, it returns 401. If valid, it returns the decoded user payload.

### Concept: FastAPI Dependencies

A dependency is just a function that FastAPI calls before your route handler. You declare it with `Depends()`. FastAPI:
1. Calls the dependency function
2. If it raises `HTTPException`, that exception is returned — your route never runs
3. If it succeeds, its return value is injected as a parameter into your route

### Concept: JWT Structure

A JWT looks like: `xxxxx.yyyyy.zzzzz`
- Header (algorithm)
- Payload (your data: user ID, email, expiry)
- Signature (proves it hasn't been tampered with)

You verify the signature using a secret key. If the signature is valid, the payload is trustworthy.

### Create `auth.py`

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

# Using python-jose because it supports multiple algorithms (RS256 for production,
# HS256 for development) with a clean API. PyJWT is also valid but has a slightly
# different interface for decoding — jose feels more ergonomic in FastAPI.
SECRET_KEY = "your-secret-key-change-in-production"   # In real code: os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

# HTTPBearer extracts the token from "Authorization: Bearer <token>"
# auto_error=False means we handle the missing token ourselves (better error messages)
http_bearer = HTTPBearer(auto_error=False)


def verify_jwt_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer)
) -> dict:
    """
    FastAPI dependency.
    - Extracts the Bearer token from the Authorization header
    - Decodes and validates the JWT
    - Returns the payload dict on success
    - Raises 401 on any failure
    """

    # No Authorization header at all
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing.",
            headers={"WWW-Authenticate": "Bearer"},   # RFC 7235 — tells the client what auth scheme to use
        )

    token = credentials.credentials   # The raw token string after "Bearer "

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload   # e.g. {"sub": "user123", "email": "alice@example.com", "exp": 1234567890}

    except JWTError:
        # JWTError covers: expired token, invalid signature, malformed token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

### Line-by-line explanation

**`HTTPBearer(auto_error=False)`**
`HTTPBearer` is a FastAPI security utility that reads the `Authorization` header and extracts everything after `"Bearer "`. With `auto_error=False`, it returns `None` instead of raising an error when the header is missing — this lets us write a cleaner error message ourselves.

**`credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer)`**
The `Depends(http_bearer)` tells FastAPI to call `http_bearer` first and pass its result here. If the header exists, `credentials.credentials` is the raw token string.

**`jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])`**
`python-jose` verifies three things automatically:
1. The signature (using `SECRET_KEY`)
2. The token hasn't expired (if the token has an `exp` claim)
3. The algorithm matches

If any check fails, it raises `JWTError`.

**`headers={"WWW-Authenticate": "Bearer"}`**
RFC 7235 standard. Tells the HTTP client what authentication scheme is required. Some clients (like browser fetch) use this header to prompt for credentials.

---

## Task 8 — Inject the Dependency into a Protected Route (Q4 Full Answer)

### In `main.py` — add this route

```python
from auth import verify_jwt_token
from fastapi import Depends

@app.get("/leads/me", response_model=dict)
async def get_my_leads(current_user: dict = Depends(verify_jwt_token)):
    """
    Protected route. 
    FastAPI calls verify_jwt_token before this function runs.
    If the token is missing/invalid, 401 is returned and this function never executes.
    current_user is the decoded JWT payload.
    """
    user_email = current_user.get("email")
    leads = await leads_collection.find({"email": user_email}).to_list(100)

    # Convert ObjectId to string for each document
    for lead in leads:
        lead["id"] = str(lead.pop("_id"))

    return {"user": user_email, "leads": leads}
```

### How `Depends()` works — the full picture

```
Incoming request to GET /leads/me
        │
        ▼
FastAPI calls Depends(verify_jwt_token)
        │
        ├─── Token missing? → raise 401 → route never runs
        ├─── Token invalid? → raise 401 → route never runs
        └─── Token valid?  → return payload dict
                                │
                                ▼
                    get_my_leads(current_user=payload)
                    runs with the decoded user data
```

### Generate a test token

```python
# Run this once in a Python shell to get a token for testing
from jose import jwt
from datetime import datetime, timedelta, timezone

SECRET_KEY = "your-secret-key-change-in-production"
ALGORITHM = "HS256"

payload = {
    "sub": "user123",
    "email": "alice@example.com",
    "exp": datetime.now(timezone.utc) + timedelta(hours=1)
}

token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
print(token)
```

```bash
# Use the token
curl http://localhost:8000/leads/me \
  -H "Authorization: Bearer <paste-token-here>"
```

---

## Q2 Quick-Reference Answer (for the interview)

> "In FastAPI, `async def` means the function is a coroutine — it can `await` I/O operations and yield control back to the event loop. `def` means FastAPI runs it in a thread pool executor automatically.
>
> I use `async def` whenever I'm awaiting a database call, HTTP call, or file read. I use `def` for pure computation with no I/O. The critical mistake is using `async def` with a synchronous blocking driver — you tell the event loop 'I've got this' and then stall it. In my Leads API, all DB routes are `async def` because Motor is async. A health check is plain `def`."

---

## Q3 Quick-Reference Answer (for the interview)

> "When I get a 422, I first read the response body — the `detail` array in FastAPI's response tells me exactly which field failed and why, including `loc` (location) and `msg` (reason). Then I open `/docs` to compare the expected schema against what I'm sending.
>
> The three most common causes I've seen:
> 1. Field name mismatch (case sensitivity in JSON — `Email` vs `email`)
> 2. Missing `Content-Type: application/json` header — FastAPI can't parse the body
> 3. Wrong type that Pydantic won't coerce — especially in strict mode or with custom validators that `raise ValueError`"

---

## Full Dependency Map

```
requests.txt
├── fastapi         — web framework, routing, dependency injection, Pydantic integration
├── uvicorn         — ASGI server that runs the async event loop
├── motor           — async MongoDB driver (wraps PyMongo for asyncio)
├── pymongo         — Motor's underlying sync driver (needed for ObjectId, etc.)
├── pydantic[email] — data validation + EmailStr type
├── python-jose     — JWT encode/decode
└── python-dotenv   — loads .env into os.environ
```

---

## What Each Interview Question is Really Testing

| Question | Surface | Real test |
|---|---|---|
| Q1 | Can you write a FastAPI endpoint? | Do you know Pydantic v2 syntax, not v1? Do you handle 409 properly vs crashing? |
| Q2 | Do you know async? | Have you been bitten by mixing sync/async? Can you explain *why*, not just *what*? |
| Q3 | Debug a 422? | Do you read error bodies, or do you guess? Do you know the 3 common causes cold? |
| Q4 | Write auth? | Do you inject dependencies properly? Do you know why `WWW-Authenticate` header exists? |

---

## Resources Checklist

Read these in order — each one maps to a task above:

- [ ] [FastAPI tutorial — Body](https://fastapi.tiangolo.com/tutorial/body/) — covers how Pydantic models work as request bodies
- [ ] [FastAPI tutorial — Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) — understand `Depends()` before Task 8
- [ ] [Pydantic v2 — Field Validators](https://docs.pydantic.dev/latest/concepts/validators/#field-validators) — `@field_validator` syntax
- [ ] [Pydantic v2 migration](https://docs.pydantic.dev/latest/migration/#validators) — only the Validators section, to know what changed from v1
- [ ] [Motor asyncio tutorial](https://motor.readthedocs.io/en/stable/tutorial-asyncio.html) — Tasks 3 and 4
- [ ] [python-jose README](https://python-jose.readthedocs.io/en/latest/) — Tasks 7 and 8
- [ ] [FastAPI — Async SQL/NoSQL](https://fastapi.tiangolo.com/async/) — the best explanation of when to use `async def` vs `def`

---

## Final Checklist Before the Interview

- [ ] Can you explain what `@field_validator` does without looking at docs?
- [ ] Can you explain why `str(result.inserted_id)` is necessary?
- [ ] Can you describe the 422 debugging process in under 60 seconds?
- [ ] Can you explain what `Depends()` does if asked mid-code?
- [ ] Can you explain the event loop analogy (chef/kitchen) without notes?
- [ ] Can you state why you chose `python-jose` over `PyJWT`?
