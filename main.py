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

