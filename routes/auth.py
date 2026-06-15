from fastapi import APIRouter, status, HTTPException
from models import UserResponse, UserCreate
from database import user_collections
from auth import hash_password, verify_password, create_access_token



router = APIRouter(prefix='/auth', tags=['auth'])


@router.post('/register', response_model=UserResponse, status_code=status.HTTP_201_CREATED)

async def register_user(user:UserCreate):
    exsisting_user = await user_collections.find_one({"email":user.email})

    if exsisting_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists."
        )
    
    hashed_password = hash_password(user.password)
    user_payload= {
        "email":user.email,
        "hashed_password":hashed_password
    }

    result = await user_collections.insert_one(user_payload)

    return UserResponse(
        id=str(result.inserted_id),
        email=user.email
    )


@router.post('/login')
async def login_user(user:UserCreate):
    db_user = await user_collections.find_one({"email":user.email})

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )
    
    if not verify_password(user.password, db_user['hashed_password']):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )
    
    token = create_access_token(data={
        "sub":str(db_user["_id"]),
        "email":db_user["email"]
    })

    return{"access_token":token, "token_type":"bearer"}