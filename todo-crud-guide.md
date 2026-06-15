# Todo CRUD App — FastAPI + MongoDB + JWT (Build From Scratch)

> **Goal:** Build a fully working Todo API with user auth end-to-end.
> Every file, every line is here. Ask me about any line and I can explain it.
> This builds on concepts from `section1-fastapi-guide.md` — read that first if you haven't.

---

## What You Will Build

```
POST   /auth/register        → create a user account
POST   /auth/login           → returns a JWT token
GET    /todos                → get all todos for the logged-in user
POST   /todos                → create a todo
GET    /todos/{id}           → get a single todo by ID
PUT    /todos/{id}           → update a todo
DELETE /todos/{id}           → delete a todo
```

Every `/todos` route is protected. No valid JWT = 401. A user can only touch **their own** todos.

---

## Final Project Structure

```
todo-api/
├── main.py           ← app instance + route registration
├── models.py         ← Pydantic models for User and Todo
├── database.py       ← Motor async MongoDB client
├── auth.py           ← password hashing, JWT creation, JWT dependency
├── routes/
│   ├── auth.py       ← /auth/register and /auth/login
│   └── todos.py      ← all /todos CRUD routes
├── .env              ← MONGO_URI, SECRET_KEY (never commit)
└── requirements.txt
```

---

## Task Plan

| #   | Task            | What you learn                                |
| --- | --------------- | --------------------------------------------- |
| 1   | Setup           | Project skeleton, dependencies                |
| 2   | Database        | Motor connection, two collections             |
| 3   | Models          | Pydantic v2 models for User and Todo          |
| 4   | Auth utilities  | Password hashing, JWT encode, JWT dependency  |
| 5   | Register route  | Hash password, store user, return 201         |
| 6   | Login route     | Verify password, generate and return JWT      |
| 7   | Create Todo     | POST /todos with ownership tied to JWT        |
| 8   | Read Todos      | GET /todos — only yours                       |
| 9   | Read one Todo   | GET /todos/{id} — 404 if missing or not yours |
| 10  | Update Todo     | PUT /todos/{id} — partial update pattern      |
| 11  | Delete Todo     | DELETE /todos/{id} — 204 no content           |
| 12  | Wire everything | main.py, routers, run and test                |

---

## Task 1 — Setup

```bash
mkdir todo-api && cd todo-api
uv init
uv venv
source .venv/bin/activate

uv add fastapi "uvicorn[standard]" motor pymongo \
       "pydantic[email]" PyJWT  "passlib[bcrypt]" python-dotenv

mkdir routes
touch main.py models.py database.py auth.py routes/__init__.py routes/auth.py routes/todos.py .env
```

### `.env`

```
MONGO_URI=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
SECRET_KEY=supersecretkey_change_this_in_production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

Get your connection string from MongoDB Atlas: **Database → Connect → Drivers → Python**.

**Why a `.env` file?**
Your `SECRET_KEY` signs every JWT in your system. If it leaks, anyone can forge tokens and impersonate any user. Your Atlas URI contains your database password. Never hardcode secrets — always load them from environment variables.

---

## Task 2 — Database Connection

### `database.py`

```python
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
db = client["todo_db"]

users_collection = db["users"]
todos_collection = db["todos"]
```

**Two collections, not one.** Users and Todos are separate MongoDB collections.

- `users` stores accounts: email, hashed password.
- `todos` stores tasks: title, done status, and a `user_id` field that links to the owner.

The `user_id` on every todo is how ownership is enforced. When a user requests their todos, you filter by `{"user_id": current_user_id}` — they can never see anyone else's data.

---

## Task 3 — Pydantic Models

### `models.py`

```python
from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional


# ── User models ───────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    """Input model for registration. Never put hashed_password here — that's server-side."""
    email: EmailStr
    password: str          # plain text — we hash it before storing


class UserInDB(BaseModel):
    """What we store in MongoDB. password field does NOT exist here."""
    id: str
    email: str
    hashed_password: str   # bcrypt hash, never the original


class UserResponse(BaseModel):
    """What we return to the client. Never expose hashed_password."""
    id: str
    email: str


# ── Todo models ───────────────────────────────────────────────────────────────

class TodoCreate(BaseModel):
    """Input model — what the user sends when creating a todo."""
    title: str
    description: Optional[str] = None   # Optional fields have a default


class TodoUpdate(BaseModel):
    """
    Update model — all fields optional because PATCH/PUT may change only some fields.
    If a field is None here, we skip updating it.
    """
    title: Optional[str] = None
    description: Optional[str] = None
    done: Optional[bool] = None


class TodoResponse(BaseModel):
    """What we return for each todo. user_id is included so the client knows the owner."""
    id: str
    title: str
    description: Optional[str] = None
    done: bool
    user_id: str

    model_config = ConfigDict(populate_by_name=True)
```

**Why separate `TodoCreate` from `TodoResponse`?**
The client sends `{title, description}`. But the response includes `id`, `done`, `user_id` — fields the server sets, not the client. Mixing these into one model either forces the client to send fields they shouldn't (like `user_id`), or forces you to mark everything Optional. Separate in/out models is the real-world pattern.

**Why `Optional[str] = None`?**
Pydantic v2: `Optional[str]` means `str | None`. The `= None` makes it a non-required field with a default. Without `= None`, even `Optional[str]` is required.

---

## Task 4 — Auth Utilities

This is the most important file. It does three things:

1. Hash passwords before storing them
2. Verify passwords at login
3. Create and verify JWTs

### `auth.py`

```python
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError

from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# passlib CryptContext handles bcrypt hashing.
# "deprecated": "auto" means it automatically upgrades old hashes if you change schemes later.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

http_bearer = HTTPBearer(auto_error=False)


# ── Password utilities ────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    """Turns 'mysecret' into '$2b$12$...' — a one-way bcrypt hash."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Returns True if plain_password hashes to the same value as hashed_password.
    bcrypt handles salting internally — you don't manage salts yourself.
    """
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT utilities ─────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Creates a signed JWT.
    'data' is the payload — typically {"sub": user_id, "email": user_email}.
    'sub' (subject) is a standard JWT claim for the user identifier.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode["exp"] = expire   # jwt.decode checks this automatically — expired token → JWTError
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ── JWT dependency (injected into protected routes) ───────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer)
) -> dict:
    """
    FastAPI dependency. Call with Depends(get_current_user).
    Returns the decoded JWT payload if the token is valid.
    Raises 401 if the token is missing, expired, or tampered with.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            # Token is valid but has no 'sub' claim — something is wrong
            raise HTTPException(status_code=401, detail="Invalid token payload.")
        return payload

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

**Why bcrypt and not SHA-256?**
SHA-256 is a fast hash — an attacker can try billions of guesses per second. bcrypt is intentionally slow (the `$12$` in the output is the cost factor — 2^12 iterations). Even with a leaked database, bcrypt hashes are extremely hard to brute-force. Never use SHA-256 or MD5 for passwords.

**Why `data.copy()`?**
`jwt.encode` will mutate the dict when adding `exp`. Copying prevents modifying the caller's dict in place — a subtle bug source.

**Why extract `sub` separately?**
`payload.get("sub")` returns `None` if the field doesn't exist. Checking for `None` explicitly catches tokens that are cryptographically valid but missing the user identifier — which would cause confusing errors later in the route.

---

## Task 5 — Register Route

### `routes/auth.py`

```python
from fastapi import APIRouter, HTTPException, status
from models import UserCreate, UserResponse
from database import users_collection
from auth import hash_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user: UserCreate):
    # 1. Check if the email is already taken
    existing = await users_collection.find_one({"email": user.email})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists."
        )

    # 2. Hash the password — NEVER store plain text
    hashed = hash_password(user.password)

    # 3. Build the document
    user_doc = {
        "email": user.email,
        "hashed_password": hashed,
    }

    # 4. Insert into MongoDB
    result = await users_collection.insert_one(user_doc)

    # 5. Return only safe fields — no hashed_password
    return UserResponse(
        id=str(result.inserted_id),
        email=user.email
    )
```

**Flow walkthrough:**

1. Client sends `{"email": "alice@example.com", "password": "secret123"}`
2. Pydantic validates email format and that both fields exist → 422 if not
3. We check MongoDB for a duplicate email → 409 if found
4. We hash the password with bcrypt → `"$2b$12$..."`
5. We insert `{email, hashed_password}` — the plain password is gone from memory
6. We return `{id, email}` — the hashed password never leaves the server

---

## Task 6 — Login Route

```python
from auth import verify_password, create_access_token

@router.post("/login")
async def login(user: UserCreate):
    # 1. Find the user
    db_user = await users_collection.find_one({"email": user.email})
    if not db_user:
        # Return 401, not 404 — don't reveal whether the email exists
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )

    # 2. Verify the password
    if not verify_password(user.password, db_user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )

    # 3. Create and return the JWT
    token = create_access_token(data={
        "sub": str(db_user["_id"]),   # user ID as the subject
        "email": db_user["email"]
    })

    return {"access_token": token, "token_type": "bearer"}
```

**Why the same error message for wrong email AND wrong password?**
If you return "email not found" vs "wrong password", an attacker can enumerate which emails are registered in your system. A generic "Invalid email or password" reveals nothing.

**Why `"sub": str(db_user["_id"])`?**
`sub` (subject) is the standard JWT claim for identifying the user. We use the MongoDB `_id` — it's guaranteed unique. The email could change in the future; the ID never does.

---

## Task 7 — Create a Todo

### `routes/todos.py`

```python
from fastapi import APIRouter, HTTPException, Depends, status
from bson import ObjectId

from models import TodoCreate, TodoUpdate, TodoResponse
from database import todos_collection
from auth import get_current_user

router = APIRouter(prefix="/todos", tags=["todos"])


def todo_from_doc(doc: dict) -> TodoResponse:
    """
    Helper: converts a MongoDB document to a TodoResponse.
    MongoDB stores _id as ObjectId — we convert to string for JSON.
    """
    return TodoResponse(
        id=str(doc["_id"]),
        title=doc["title"],
        description=doc.get("description"),
        done=doc["done"],
        user_id=doc["user_id"],
    )


@router.post("", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
async def create_todo(
    todo: TodoCreate,
    current_user: dict = Depends(get_current_user)   # JWT dependency injected here
):
    """
    Creates a todo owned by the authenticated user.
    current_user comes from the decoded JWT payload.
    """
    doc = {
        "title": todo.title,
        "description": todo.description,
        "done": False,                        # New todos always start as not done
        "user_id": current_user["sub"],       # "sub" is the user ID we put in the token
    }
    result = await todos_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return todo_from_doc(doc)
```

**Why `current_user["sub"]`?**
When we created the token in the login route, we set `"sub": str(db_user["_id"])`. `get_current_user` decodes that token and returns the payload dict. So `current_user["sub"]` is the user's MongoDB ID — we embed it in every todo as `user_id`.

**Why `Depends(get_current_user)` on the parameter?**
FastAPI reads the function signature. Seeing `Depends(get_current_user)`, it calls `get_current_user` before `create_todo` runs. If the token is missing or invalid, `get_current_user` raises 401 and `create_todo` never executes.

---

## Task 8 — Get All Todos

```python
from typing import List

@router.get("", response_model=List[TodoResponse])
async def get_todos(current_user: dict = Depends(get_current_user)):
    """
    Returns all todos belonging to the current user.
    The filter {"user_id": ...} ensures users can never see each other's todos.
    """
    cursor = todos_collection.find({"user_id": current_user["sub"]})
    todos = await cursor.to_list(length=100)   # cap at 100 — add pagination for production
    return [todo_from_doc(t) for t in todos]
```

**Why `.to_list(length=100)`?**
Motor's `find()` returns a cursor — it doesn't fetch documents yet. `.to_list(length=N)` materializes up to N documents into a Python list. Without a limit, a user with 100,000 todos would pull everything into RAM at once. For production, use skip/limit-based pagination instead.

**Why filter by `user_id`?**
Without `{"user_id": current_user["sub"]}`, every authenticated user would see every todo in the database. The JWT tells you who is asking — the filter enforces that they only see what's theirs.

---

## Task 9 — Get One Todo

```python
@router.get("/{todo_id}", response_model=TodoResponse)
async def get_todo(todo_id: str, current_user: dict = Depends(get_current_user)):
    # Validate that todo_id is a valid ObjectId string before querying
    if not ObjectId.is_valid(todo_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid todo ID format.")

    todo = await todos_collection.find_one({
        "_id": ObjectId(todo_id),
        "user_id": current_user["sub"]   # ownership check in the same query
    })

    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found.")

    return todo_from_doc(todo)
```

**Why check both `_id` AND `user_id` in the same query?**
If you query only by `_id` and then check ownership separately, there's a window where you return "Todo not found" for the wrong reason. More importantly, an attacker who guesses another user's todo ID gets back "todo not found" — not "you don't own this" — so they learn nothing. The combined query is both safer and simpler.

**Why `ObjectId.is_valid(todo_id)`?**
MongoDB's `ObjectId` is a 24-character hex string. If you call `ObjectId("not-valid")`, bson raises a `InvalidId` exception that FastAPI doesn't catch — your app returns a 500 instead of a clean 400. Always validate before converting.

---

## Task 10 — Update a Todo

```python
@router.put("/{todo_id}", response_model=TodoResponse)
async def update_todo(
    todo_id: str,
    updates: TodoUpdate,
    current_user: dict = Depends(get_current_user)
):
    if not ObjectId.is_valid(todo_id):
        raise HTTPException(status_code=400, detail="Invalid todo ID format.")

    # Build the update dict — skip fields that are None (not sent by the client)
    update_data = {k: v for k, v in updates.model_dump().items() if v is not None}

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided to update.")

    result = await todos_collection.find_one_and_update(
        {"_id": ObjectId(todo_id), "user_id": current_user["sub"]},  # filter
        {"$set": update_data},                                         # what to change
        return_document=True                                           # return the updated doc
    )

    if not result:
        raise HTTPException(status_code=404, detail="Todo not found.")

    return todo_from_doc(result)
```

**Why `{k: v for k, v in updates.model_dump().items() if v is not None}`?**
`TodoUpdate` has all fields Optional. If the client sends `{"done": true}`, `model_dump()` gives `{"title": None, "description": None, "done": True}`. Without the filter, `$set` would overwrite `title` and `description` with `null`. We only set fields the client actually sent.

**Why `find_one_and_update` instead of `update_one`?**
`update_one` returns an `UpdateResult` with a count — you'd need a second query to get the updated document. `find_one_and_update` with `return_document=True` returns the updated document in one atomic operation.

**Why `$set`?**
MongoDB's `$set` operator updates only the specified fields. Without it — if you passed `{"title": "new"}` directly — MongoDB would _replace the entire document_ with just `{"title": "new"}`, deleting all other fields.

---

## Task 11 — Delete a Todo

```python
@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: str, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(todo_id):
        raise HTTPException(status_code=400, detail="Invalid todo ID format.")

    result = await todos_collection.delete_one({
        "_id": ObjectId(todo_id),
        "user_id": current_user["sub"]
    })

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found.")

    # 204 No Content — return nothing, not even an empty dict
```

**Why 204 and not 200?**
204 No Content is the standard HTTP response for a successful delete. It signals "it worked, and there's nothing to return." Returning `{}` with 200 is also common but 204 is semantically cleaner.

**Why check `deleted_count == 0`?**
`delete_one` never raises an error if nothing matched — it silently deletes zero documents. Without checking `deleted_count`, you'd return 204 even when the todo didn't exist or didn't belong to the user. The check makes the behavior explicit.

---

## Task 12 — Wire Everything Together

### `main.py`

```python
from fastapi import FastAPI
from routes.auth import router as auth_router
from routes.todos import router as todos_router

app = FastAPI(title="Todo API", version="1.0.0")

# Register routers — prefixes are defined in the router files
app.include_router(auth_router)    # /auth/register, /auth/login
app.include_router(todos_router)   # /todos, /todos/{id}


@app.get("/")
async def root():
    return {"message": "Todo API is running. Go to /docs for the interactive API."}
```

**Why `include_router`?**
Splitting routes into separate files and including them in `main.py` keeps the codebase navigable. If all routes lived in `main.py`, it would grow to hundreds of lines. `include_router` mounts all the routes from a router onto the app without you listing each one.

---

## Run the App

```bash
# Start the API with auto-reload (Atlas is cloud-hosted — no local mongod needed)
uvicorn main:app --reload

# Open the interactive docs in your browser
open http://localhost:8000/docs
```

---

## Manual Test Flow (copy-paste these in order)

```bash
# 1. Register
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "secret123"}' | python3 -m json.tool

# 2. Login — copy the access_token from the response
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "secret123"}' | python3 -m json.tool

# Set the token (paste your actual token)
TOKEN="paste_your_token_here"

# 3. Create a todo
curl -s -X POST http://localhost:8000/todos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Buy groceries", "description": "Milk and eggs"}' | python3 -m json.tool

# 4. Get all todos
curl -s http://localhost:8000/todos \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 5. Update a todo (paste actual todo id)
TODO_ID="paste_todo_id_here"
curl -s -X PUT http://localhost:8000/todos/$TODO_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"done": true}' | python3 -m json.tool

# 6. Delete a todo
curl -s -X DELETE http://localhost:8000/todos/$TODO_ID \
  -H "Authorization: Bearer $TOKEN"
# Expected: no body, HTTP 204
```

---

## How the Auth Flow Works End-to-End

```
Register
  Client → POST /auth/register {email, password}
         → Server hashes password with bcrypt
         → Stores {email, hashed_password} in users collection
         → Returns {id, email}   ← no password ever leaves server

Login
  Client → POST /auth/login {email, password}
         → Server finds user by email
         → bcrypt.verify(plain_password, hashed_password)
         → Creates JWT: {"sub": user_id, "email": email, "exp": ...}
         → Signs JWT with SECRET_KEY
         → Returns {"access_token": "xxx.yyy.zzz", "token_type": "bearer"}

Protected Request
  Client → GET /todos
           Authorization: Bearer xxx.yyy.zzz
         → HTTPBearer extracts "xxx.yyy.zzz"
         → jwt.decode verifies signature + expiry
         → Returns payload {"sub": user_id, "email": email}
         → Route runs with current_user = that payload
         → MongoDB query filters by user_id = current_user["sub"]
         → Returns only this user's todos
```

---

## Common Mistakes and How to Avoid Them

| Mistake                                        | Symptom                                                       | Fix                                                     |
| ---------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------- |
| Storing plain-text password                    | Security disaster                                             | Always call `hash_password()` before `insert_one`       |
| Not converting `_id` to string                 | `TypeError: Object of type ObjectId is not JSON serializable` | `str(doc["_id"])` always                                |
| Querying without `user_id` filter              | Users see each other's todos                                  | Include `"user_id": current_user["sub"]` in every query |
| Using `$set` wrong                             | Entire document replaced                                      | Always wrap updates in `{"$set": update_data}`          |
| Not checking `ObjectId.is_valid`               | Unhandled 500 on bad ID                                       | Validate before `ObjectId(todo_id)`                     |
| Calling `verify_password` with wrong arg order | Always returns False                                          | Signature is `verify(plain, hashed)` — order matters    |
| Returning `hashed_password` in response        | Exposes sensitive data                                        | Use `UserResponse` model, not `UserInDB`                |

---

## Key Concepts Summary

**Dependency Injection (`Depends`)**
FastAPI calls the dependency before the route. If it raises, the route never runs. Multiple routes can share the same dependency — you write `get_current_user` once and inject it everywhere.

**JWT flow**
Login → server signs a token with SECRET_KEY → client sends token on every request → server verifies signature → extracts user ID → filters data by that user ID. The server never stores sessions; the token is self-contained.

**bcrypt**
One-way hash with a built-in random salt. `verify()` re-hashes the input and compares — you never un-hash. The cost factor (the `12` in `$2b$12$...`) makes it slow enough to be useless for brute-force.

**Ownership pattern**
Every resource has a `user_id` field. Every query includes `{"user_id": current_user["sub"]}`. This is the simplest, most reliable way to isolate user data in a single-tenant MongoDB schema.

---

## Resources

- [ ] [FastAPI — Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/) — explains `APIRouter` and `include_router`
- [ ] [FastAPI — Security / OAuth2 with JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/) — the official JWT tutorial (uses a different structure but same concepts)
- [ ] [passlib bcrypt docs](https://passlib.readthedocs.io/en/stable/lib/passlib.hash.bcrypt.html) — understand cost factor and why bcrypt
- [ ] [python-jose README](https://python-jose.readthedocs.io/en/latest/) — `jwt.encode` and `jwt.decode` parameters
- [ ] [MongoDB `$set` operator](https://www.mongodb.com/docs/manual/reference/operator/update/set/) — why you always need it for updates
- [ ] [HTTP status codes reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status) — know 200/201/204/400/401/403/404/409/422 cold
