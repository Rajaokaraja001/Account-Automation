import asyncio
import random
import string
import requests
import re
import time
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright

app = FastAPI()

# ===========================================================
# !!! EDIT THIS SECTION FOR YOUR TARGET WEBSITE !!!
# ===========================================================
SITE_CONFIG = {
    "url": "https://example.com/signup",           # CHANGE THIS to the real signup page
    "email_selector": "input[name='email']",       # CHANGE THIS (inspect the page to find it)
    "password_selector": "input[name='password']", # CHANGE THIS
    "confirm_selector": "input[name='confirm_password']", # CHANGE THIS (delete line if no confirm box)
    "submit_selector": "button[type='submit']",    # CHANGE THIS
    "success_text": "Account activated"            # Text that appears after clicking verification link
}
# ===========================================================

MAILTM_API = "https://api.mail.tm"

class RegisterRequest(BaseModel):
    password: str

async def create_temp_email():
    # Get a working domain from mail.tm
    domains = requests.get(f"{MAILTM_API}/domains").json()
    domain = domains['hydra:member'][0]['domain']
    
    # Generate random username
    user = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{user}@{domain}"
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=15))
    
    # Create the inbox
    payload = {"address": email, "password": password}
    resp = requests.post(f"{MAILTM_API}/accounts", json=payload)
    if resp.status_code != 201:
        raise Exception("Mail.tm account creation failed")
    
    # Get authentication token
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
                # Get the most recent email
                msg_id = msgs[0]['id']
                msg_resp = requests.get(f"{MAILTM_API}/messages/{msg_id}", headers=headers)
                data = msg_resp.json()
                
                # Mail.tm often stores body as base64 or HTML array
                body = data.get('html', [''])[0] if data.get('html') else data.get('text', '')
                if isinstance(body, list):
                    body = ''.join(body)
                
                # Extract all links
                links = re.findall(r'https?://[^\s"\'<>]+', body)
                if links:
                    return links[0]  # Return the first verification link
        except Exception:
            pass  # Ignore temporary API glitches
        
        await asyncio.sleep(4)  # Wait 4 seconds before checking again
    
    return None  # Timeout

@app.post("/register")
async def register_account(request: RegisterRequest):
    try:
        # Step A: Create temporary email
        temp_email, token = await create_temp_email()
        
        # Step B: Launch Playwright browser and register on the target site
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            
            # Go to the signup page
            await page.goto(SITE_CONFIG["url"], timeout=30000)
            await page.wait_for_load_state("networkidle")
            
            # Fill the form
            await page.fill(SITE_CONFIG["email_selector"], temp_email)
            await page.fill(SITE_CONFIG["password_selector"], request.password)
            
            # If there is a confirm password field, fill it
            if SITE_CONFIG.get("confirm_selector"):
                await page.fill(SITE_CONFIG["confirm_selector"], request.password)
            
            # Click submit
            await page.click(SITE_CONFIG["submit_selector"])
            await page.wait_for_load_state("networkidle")
            
            # Step C: Wait for the verification email to arrive
            verify_link = await wait_for_verification(token)
            if not verify_link:
                await browser.close()
                return {"success": False, "error": "Verification email timeout", "email": temp_email}
            
            # Step D: Click the verification link in the browser
            await page.goto(verify_link)
            await page.wait_for_load_state("networkidle")
            
            # Check if verification was successful
            page_content = await page.content()
            success = SITE_CONFIG["success_text"].lower() in page_content.lower()
            
            await browser.close()
            return {
                "success": success,
                "email": temp_email,
                "password": request.password,
                "link": verify_link
            }
            
    except Exception as e:
        return {"success": False, "error": str(e), "email": None}

@app.get("/")
def root():
    return {"message": "Account Registration API is running"}
