# main.py
import asyncio
import random
import string
import requests
import re
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from playwright.async_api import async_playwright

app = FastAPI()

# ==================================================
# !!! EDIT THIS SECTION FOR YOUR TARGET WEBSITE !!!
# ==================================================
SITE_CONFIG = {
    "url": "https://example.com/signup",          # CHANGE THIS
    "email_selector": "input[name='email']",      # CHANGE THIS
    "password_selector": "input[name='password']",# CHANGE THIS
    "confirm_selector": "input[name='confirm_password']", 
    "submit_selector": "button[type='submit']",   
    "success_text": "Account activated"           
}
# ==================================================

MAILTM_API = "https://api.mail.tm"

class RegisterRequest(BaseModel):
    password: str

async def create_temp_email():
    domains = requests.get(f"{MAILTM_API}/domains").json()
    domain = domains['hydra:member'][0]['domain']
    user = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{user}@{domain}"
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=15))
    payload = {"address": email, "password": password}
    resp = requests.post(f"{MAILTM_API}/accounts", json=payload)
    if resp.status_code != 201:
        raise Exception("Mail.tm failed")
    token_resp = requests.post(f"{MAILTM_API}/token", json={"address": email, "password": password})
    token = token_resp.json()['token']
    return email, token

async def wait_for_verification(token, timeout=90):
    headers = {"Authorization": f"Bearer {token}"}
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            resp = requests.get(f"{MAILTM_API}/messages", headers=headers)
            msgs = resp.json().get('hydra:member', [])
            if msgs:
                msg_id = msgs[0]['id']
                msg_resp = requests.get(f"{MAILTM_API}/messages/{msg_id}", headers=headers)
                data = msg_resp.json()
                body = data.get('html', [''])[0] if data.get('html') else data.get('text', '')
                if isinstance(body, list):
                    body = ''.join(body)
                links = re.findall(r'https?://[^\s"\'<>]+', body)
                if links:
                    return links[0]
        except Exception:
            pass
        await asyncio.sleep(4)
    return None

@app.post("/register")
async def register_account(request: RegisterRequest):
    try:
        temp_email, token = await create_temp_email()
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            
            await page.goto(SITE_CONFIG["url"], timeout=30000)
            await page.wait_for_load_state("networkidle")
            await page.fill(SITE_CONFIG["email_selector"], temp_email)
            await page.fill(SITE_CONFIG["password_selector"], request.password)
            if SITE_CONFIG.get("confirm_selector"):
                await page.fill(SITE_CONFIG["confirm_selector"], request.password)
            await page.click(SITE_CONFIG["submit_selector"])
            await page.wait_for_load_state("networkidle")
            
            verify_link = await wait_for_verification(token)
            if not verify_link:
                await browser.close()
                return {"success": False, "error": "Verification timeout", "email": temp_email}
            
            await page.goto(verify_link)
            await page.wait_for_load_state("networkidle")
            page_content = await page.content()
            success = SITE_CONFIG["success_text"].lower() in page_content.lower()
            await browser.close()
            
            return {"success": success, "email": temp_email, "password": request.password, "link": verify_link}
    except Exception as e:
        return {"success": False, "error": str(e), "email": None}

@app.get("/")
def root():
    return {"message": "Account Registration API is running"}