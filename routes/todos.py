from fastapi import status ,HTTPException , APIRouter, Depends
from models import TodoCreate, TodoResponse, TodoUpdate
from auth import get_current_user
from database import todos_collections
from typing import List
from bson import ObjectId


router = APIRouter(prefix='/todos', tags=['todos'])

def todo_from_doc(doc:dict)->TodoResponse:
    return TodoResponse(
        id=str(doc["_id"]),
        title=doc["title"],
        description=doc.get("description"),
        is_done=doc["is_done"],
        user_id=doc["user_id"],
    )

# Create todos
@router.post("",response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
async def create_todos(todo:TodoCreate,current_user:dict=Depends(get_current_user)):
    doc ={
        "title": todo.title,
        "description": todo.description,
        "is_done":False,
        "user_id":current_user['sub']
    }

    result = await todos_collections.insert_one(doc)
    doc["_id"] = result.inserted_id
    return todo_from_doc(doc)


# Get all todos 
@router.get("", response_model=List[TodoResponse])
async def get_all_todos(current_user:dict=Depends(get_current_user)):

    res =  todos_collections.find({"user_id":current_user['sub']})
    todos = await res.to_list()

    return [todo_from_doc(t) for t in todos]


# Get one todo with id
@router.get("/{todo_id}", response_model=TodoResponse)
async def get_todo(todo_id: str, current_user: dict = Depends(get_current_user)):
    # Validate that todo_id is a valid ObjectId string before querying
    if not ObjectId.is_valid(todo_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid todo ID format.")

    todo = await todos_collections.find_one({
        "_id": ObjectId(todo_id),
        "user_id": current_user["sub"]   # ownership check in the same query
    })

    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found.")

    return todo_from_doc(todo)


# Update todo
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

    result = await todos_collections.find_one_and_update(
        {"_id": ObjectId(todo_id), "user_id": current_user["sub"]},  # filter
        {"$set": update_data},                                         # what to change
        return_document=True                                           # return the updated doc
    )

    if not result:
        raise HTTPException(status_code=404, detail="Todo not found.")

    return todo_from_doc(result)


# Delete a todo
@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: str, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(todo_id):
        raise HTTPException(status_code=400, detail="Invalid todo ID format.")

    result = await todos_collections.delete_one({
        "_id": ObjectId(todo_id),
        "user_id": current_user["sub"]
    })

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found.")