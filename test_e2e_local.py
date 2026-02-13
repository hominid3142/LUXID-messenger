import time
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        # 브라우저 실행 (headless=False 옵션으로 브라우저가 뜨는 것을 볼 수 있음)
        browser = p.chromium.launch(headless=False, slow_mo=500)
        page = browser.new_page()

        print("1. 메인 페이지 접속")
        page.goto("http://localhost:5000")
        
        # 스플래시 화면 대기
        page.wait_for_selector("#splash-screen", state="hidden", timeout=5000)
        
        print("2. 시작하기 버튼 클릭")
        page.click(".start-btn")
        
        print("3. 회원가입 화면으로 전환")
        page.click(".auth-link")  # '계정이 없으신가요?' 클릭
        
        # 유니크한 아이디 생성
        username = f"e2e_test_{int(time.time())}"
        print(f"4. 회원가입 진행 (ID: {username})")
        
        page.fill("#reg-username", username)
        page.fill("#reg-password", "1234")
        
        # 다이얼로그(alert) 자동 수락 핸들러
        page.on("dialog", lambda dialog: dialog.accept())
        
        page.click("#register-form .auth-btn-main") # 회원가입 버튼
        
        print("5. 프로필 작성 화면 대기")
        page.wait_for_selector("#profile-setup-overlay", state="visible")
        
        print("6. 프로필 정보 입력")
        page.fill("#profile-display-name", "자동화테스트유저")
        
        print("7. 프로필 저장")
        page.click("#profile-setup-overlay .auth-btn-main") # 완료 버튼
        
        print("8. 메인 화면 진입 대기")
        page.wait_for_selector("#friend-list", state="visible")
        
        print("9. 설정 탭으로 이동")
        # 설정 탭 아이콘 클릭 (Settings 텍스트를 포함하거나 3번째 nav-item)
        page.click(".nav-item:nth-child(3)") 
        
        print("10. 테마 변경 (Dark -> Light)")
        page.select_option("#theme-select", "light")
        
        # CSS 클래스 변경 확인
        is_light = page.evaluate("document.body.classList.contains('light-mode')")
        if is_light:
            print(">> [성공] Light Mode 적용 확인됨!")
        else:
            print(">> [실패] Light Mode 적용 안됨")
            
        # 스크린샷 저장
        page.screenshot(path="e2e_result.png")
        print("11. 테스트 완료 (스크린샷 저장됨: e2e_result.png)")
        
        time.sleep(3) # 사용자가 결과를 볼 수 있게 잠시 대기
        browser.close()

if __name__ == "__main__":
    run()
