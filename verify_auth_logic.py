
import requests
import json
import time

BASE_URL = "http://127.0.0.1:5003"
ADMIN_USER = "admin"
ADMIN_PASS = "31313142"

def verify_auth():
    print(f"Attempting login to {BASE_URL}/login...")
    try:
        res = requests.post(f"{BASE_URL}/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
        if res.status_code != 200:
            print(f"Login failed: {res.status_code} {res.text}")
            return
        
        data = res.json()
        token = data.get("access_token")
        is_admin = data.get("is_admin")
        print(f"Login successful. Token acquired. IsAdmin: {is_admin}")
        
        if not is_admin:
            print("WARNING: Login user is NOT admin according to response.")
        
        headers = {"Authorization": f"Bearer {token}"}
        
        # Test Batch Create
        print("Testing /admin/batch-create-eves...")
        payload = {"count": 2, "white": 0, "black": 0, "asian": 100}
        res = requests.post(f"{BASE_URL}/admin/batch-create-eves", json=payload, headers=headers)
        
        if res.status_code == 200:
            job_data = res.json()
            job_id = job_data.get("job_id")
            print(f"Batch creation started. JobID: {job_id}")
            
            # Poll status
            for _ in range(5):
                time.sleep(1)
                res = requests.get(f"{BASE_URL}/admin/batch-status/{job_id}", headers=headers)
                status = res.json()
                print(f"Status: {status.get('created')}/{status.get('total')} (Failed: {status.get('failed')})")
                if status.get('done'):
                    print("Job done!")
                    break
        else:
            print(f"Batch create failed: {res.status_code} {res.text}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    verify_auth()
