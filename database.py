import os
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv()


client = AsyncIOMotorClient(
    os.environ.get("MONGO_URI"), 
    tls=True,
    tlsCAFile=certifi.where()
)

db = client["todo_db"]

user_collections = db["users"]
todos_collections = db["todos"]