from pydantic import BaseModel,EmailStr, ConfigDict, Field, field_validator
from typing import Optional


#-------------------------- User Models ------------------------------

class UserBase(BaseModel):
    email:EmailStr

class UserCreate(UserBase):
    password:str

    @field_validator("password")
    @classmethod
    def password_validator(cls , value):
        if len(value) < 8:
            raise ValueError("Password length should be atleast 8")
        return value

class UserInDB(UserBase):
    id :str
    hashed_password:str

class UserResponse(UserBase):
    id:str

#-----------------------------------------------------------------------

#--------------------- Todo Models -------------------------------------

class TodoCreate(BaseModel):
    title:str=Field(min_length=1)
    description:Optional[str]=None

class TodoUpdate(BaseModel):
    title:Optional[str]=None
    description : Optional[str] = None
    is_done : Optional[bool] = None

class TodoResponse(BaseModel):
    id : str
    title:str
    description:Optional[str]= None
    is_done:bool=Field(default=False)
    user_id:str

    model_config = ConfigDict(populate_by_name=True)


