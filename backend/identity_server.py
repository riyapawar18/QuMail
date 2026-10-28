import os
import uvicorn
from fastapi import FastAPI, Form, HTTPException
from pydantic import BaseModel

class RegistrationData(BaseModel):
    email: str
    level3_pk: str
    level4_pk: str

app = FastAPI(title="QuMail Identity Registry")
USER_REGISTRY = {}
@app.get("/")
async def root():
    return {"status": "QuMail Identity Server running"}

@app.post("/register")
async def register(data: RegistrationData): # Changed from Form(...) to our new Model
    email = data.email.strip().lower()
    USER_REGISTRY[email] = {
        "3": data.level3_pk,
        "4": data.level4_pk
    }
    return {"status": "success", "detail": f"Keys rotated for {email}"}

@app.get("/lookup/{email}")
async def lookup(email: str):
    email = email.strip().lower()
    if email not in USER_REGISTRY:
        raise HTTPException(status_code=404, detail="User not found")
    return USER_REGISTRY[email]

if __name__ == "__main__":
    # Render provides the PORT environment variable
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)