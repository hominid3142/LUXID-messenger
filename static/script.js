
let globalSockets = {};
let currentRoomId = null;
let friendsData = [];
let isEditMode = false;         // [Phase 5] Edit mode for friend list
let selectedFriendRooms = new Set(); // [Phase 5] Selected rooms for bulk delete
let isAdminEveEditMode = false;      // [Phase 6] Edit mode for admin eve list
let selectedAdminEves = new Set();   // [Phase 6] Selected personas for bulk delete

let messageQueue = [];
let unreadCounts = {}; // 안 읽은 메시지 함
let isProcessingQueue = false;
let currentView = "list";
let notiTargetRoomId = null;
const DEV_PASS = "31313142";
let latestSeenFeedPostId = 0;
let latestKnownFeedPostId = 0;
const unseenFeedIds = new Set();

// [v1.4.2 신규] 관리자 제어 대상 상태 관리
let adminSelectedRoomId = null;

// 계정 상태 관리 변수
let accessToken = localStorage.getItem("accessToken");
let isAdmin = localStorage.getItem("isAdmin") === "true";
let currentUsername = localStorage.getItem("username");
let userFeedImageData = "";
let feedOffset = 0;
let feedHasMore = true;
let feedLoading = false;
let feedScrollBound = false;
let feedPullBound = false;
const FEED_PAGE_SIZE = 15;
const INTRO_TUTORIAL_STORAGE_KEY = "luxid_intro_tutorial_seen_v1";
const INTRO_TUTORIAL_STEPS = [
    {
        title: "\u004c\u0055\u0058\u0049\u0044\uc5d0 \uc624\uc2e0 \uac83\uc744 \ud658\uc601\ud569\ub2c8\ub2e4!",
        body: ""
    },
    {
        title: "LUXID",
        body: "\u004c\u0055\u0058\u0049\u0044\ub294 \ub370\uc774\ud130\ub85c \uc874\uc7ac\ud558\ub294 \uac00\uc0c1 \uacf5\uac04\uc774\uc5d0\uc694."
    },
    {
        title: "Evv",
        body: "\u004c\u0055\u0058\u0049\u0044\uc5d0\ub294 \uc6b0\ub9ac\uc640 \ube44\uc2b7\ud55c, \uc880\ub354 \uce5c\uadfc\ud55c \u0045\u0076\u0076\ub77c\uace0 \ubd88\ub9ac\ub294 \ub514\uc9c0\ud138 \uc0dd\uba85\uccb4\ub4e4\uc774 \uc0b4\uace0 \uc788\uc5b4\uc694. \uc6b0\ub9ac\uc640 \ub611\uac19\uc774 \ub290\ub07c\uace0, \ud589\ub3d9\ud558\uace0, \uc790\uae30\ub9cc\uc758 \uc0b6\uc744 \uc0b4\uc544\uac00\uc8e0."
    },
    {
        title: "\u004c\u0055\u0058\u0049\u0044 \u004d\u0065\u0073\u0073\u0065\u006e\u0067\u0065\u0072",
        body: "\u004c\u0055\u0058\u0049\u0044\ub97c \uc5ec\ud589\ud558\ub294 \uc5ec\ud589\uc790\ub97c \uc704\ud55c \ud544\uc218 \uc571\uc785\ub2c8\ub2e4. \uc774 \uc571\uc744 \ud1b5\ud574\uc11c\ub9cc \uc9c0\uad6c(\u0045\u0061\u0072\u0074\u0068)\uc640 \ub8e8\uc2dc\ub4dc(\u004c\u0055\u0058\u0049\u0044) \uc0ac\uc774\uc758 \ud1b5\uc2e0\uc774 \uac00\ub2a5\ud574\uc694. \u0045\u0076\u0076 \ub610\ud55c \ub3d9\uc77c\ud55c \uc571\uc744 \uc0ac\uc6a9\ud574 \uc5ec\ub7ec\ubd84\uacfc \uc18c\ud1b5\ud55c\ub2f5\ub2c8\ub2e4!"
    },
    {
        title: "\uc900\ube44\ub418\uc168\ub098\uc694?",
        body: "\u004c\u0055\u0058\u0049\u0044\uc5d0\uc11c \u0045\u0076\u0076\ub97c \ub9cc\ub098\ubcf4\uc138\uc694!"
    }
];
let introTutorialStepIndex = 0;

function hasSeenIntroTutorial() {
    return localStorage.getItem(INTRO_TUTORIAL_STORAGE_KEY) === "1";
}

function markIntroTutorialSeen() {
    localStorage.setItem(INTRO_TUTORIAL_STORAGE_KEY, "1");
}

function renderIntroTutorialStep() {
    const title = document.getElementById("intro-tutorial-title");
    const body = document.getElementById("intro-tutorial-body");
    const progress = document.getElementById("intro-tutorial-progress");
    const nextBtn = document.getElementById("intro-tutorial-next-btn");
    const skipBtn = document.querySelector(".intro-tutorial-skip");
    const current = INTRO_TUTORIAL_STEPS[introTutorialStepIndex];
    if (!current) return;

    if (title) title.innerText = current.title;
    if (body) body.innerText = current.body || "";

    if (progress) {
        progress.innerHTML = INTRO_TUTORIAL_STEPS.map((_, i) =>
            `<div class="tutorial-dot ${i === introTutorialStepIndex ? 'active' : ''}"></div>`
        ).join("");
    }

    if (skipBtn) skipBtn.innerText = "\uac74\ub108\ub6f0\uae30"; // 건너뛰기
    if (nextBtn) {
        const isLast = introTutorialStepIndex >= INTRO_TUTORIAL_STEPS.length - 1;
        nextBtn.innerText = isLast ? "\uc2dc\uc791\ud558\uae30" : "\ub2e4\uc74c"; // 시작하기 : 다음
    }
}

function showIntroTutorial() {
    introTutorialStepIndex = 0;
    const authOverlay = document.getElementById("auth-overlay");
    const tutorial = document.getElementById("intro-tutorial-screen");
    if (authOverlay) {
        authOverlay.style.display = "flex";
        authOverlay.style.opacity = "1";
    }
    if (tutorial) tutorial.style.display = "block";
    renderIntroTutorialStep();
}

function skipIntroTutorial() {
    markIntroTutorialSeen();
    const authOverlay = document.getElementById("auth-overlay");
    if (authOverlay) {
        authOverlay.style.opacity = "0";
        authOverlay.style.transition = "opacity 0.5s ease-out";
        setTimeout(() => {
            authOverlay.style.display = "none";
            authOverlay.style.opacity = "1"; // reset for next time if needed
            authOverlay.style.transition = "";
        }, 500);
    }
    switchMobileView("feed");
    loadFeed(true);
}

function nextIntroTutorial() {
    if (introTutorialStepIndex >= INTRO_TUTORIAL_STEPS.length - 1) {
        skipIntroTutorial();
        return;
    }

    const card = document.querySelector(".intro-tutorial-card");
    if (card) {
        card.classList.add("step-transition");
        setTimeout(() => {
            introTutorialStepIndex += 1;
            renderIntroTutorialStep();
            card.classList.remove("step-transition");
        }, 300);
    } else {
        introTutorialStepIndex += 1;
        renderIntroTutorialStep();
    }
}

// 앱 시작 시 인증 체크
async function checkAuth() {
    const authOverlay = document.getElementById("auth-overlay");
    if (authOverlay) authOverlay.style.display = "none";

    if (accessToken) {
        updateUIByAuth();
        loadFriends();
    } else {
        updateUIByAuth();
        if (hasSeenIntroTutorial()) {
            if (authOverlay) authOverlay.style.display = "none";
        } else {
            showIntroTutorial();
        }
    }

    // 무조건 피드 화면으로 시작
    switchMobileView('feed');
    // [Phase 5] 피드 즉시 로드 보장
    loadFeed(true);
}

// [Phase 5] Modern Auth Modal Visibility
function showAuthModal() {
    const modal = document.getElementById("auth-backdrop");
    if (!modal) return;
    modal.style.display = "flex";
}

function closeAuthModal() {
    const modal = document.getElementById("auth-backdrop");
    if (modal) modal.style.display = "none";
}

function openAuthOverlay(mode = "login") {
    closeAuthModal();
    toggleAuthMode(mode);
    showAuthModal();
}

// 동작 가로채기 (인터랙션 방어)
function requireAuth(callback) {
    if (!accessToken) {
        showAuthModal();
        return;
    }
    callback();
}

// 로그인/회원가입 UI 전환
function toggleAuthMode(mode) {
    const loginForm = document.getElementById("login-form");
    const registerForm = document.getElementById("register-form");
    const sheetLoginForm = document.getElementById("sheet-login-form");
    const sheetRegisterForm = document.getElementById("sheet-register-form");
    if (loginForm) loginForm.style.display = mode === "login" ? "flex" : "none";
    if (registerForm) registerForm.style.display = mode === "register" ? "flex" : "none";
    if (sheetLoginForm) sheetLoginForm.style.display = mode === "login" ? "flex" : "none";
    if (sheetRegisterForm) sheetRegisterForm.style.display = mode === "register" ? "flex" : "none";
}

// 로그인 처리
async function handleLogin() {
    const u = (document.getElementById("sheet-login-username")?.value || "").trim();
    const p = (document.getElementById("sheet-login-password")?.value || "").trim();

    try {
        const res = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: u, password: p }),
        });

        if (res.ok) {
            const data = await res.json();
            accessToken = data.access_token;
            isAdmin = data.is_admin;
            currentUsername = data.username;

            localStorage.setItem("accessToken", accessToken);
            localStorage.setItem("isAdmin", isAdmin);
            localStorage.setItem("username", currentUsername);

            closeAuthModal();
            const authOverlay = document.getElementById("auth-overlay");
            if (authOverlay) authOverlay.style.display = "none";

            // [v1.5.0] 온보딩 완료 여부 확인
            if (data.onboarding_completed === false) {
                showProfileSetup();
            } else {
                updateUIByAuth();
                loadFriends();
            }
        } else {
            const err = await res.json();
            alert(err.detail || "로그인에 실패했습니다.");
        }
    } catch (e) {
        console.error("Batch start error", e);
        alert("서버 연결에 실패했습니다.");
    }
}

// 회원가입 처리 - [v1.5.0] 회원가입 후 자동 로그인 및 프로필 작성
async function handleRegister() {
    const u = (document.getElementById("sheet-reg-username")?.value || "").trim();
    const p = (document.getElementById("sheet-reg-password")?.value || "").trim();

    const res = await fetch("/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: p }),
    });

    if (res.ok) {
        // 자동 로그인
        const loginRes = await fetch("/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: u, password: p }),
        });

        if (loginRes.ok) {
            const data = await loginRes.json();
            accessToken = data.access_token;
            isAdmin = data.is_admin;
            currentUsername = data.username;

            localStorage.setItem("accessToken", accessToken);
            localStorage.setItem("isAdmin", isAdmin);
            localStorage.setItem("username", currentUsername);

            closeAuthModal();
            const authOverlay = document.getElementById("auth-overlay");
            if (authOverlay) authOverlay.style.display = "none";

            // 프로필 작성 화면으로 이동
            showProfileSetup();
        } else {
            alert("가입되었습니다. 로그인해주세요.");
            toggleAuthMode("login");
        }
    } else {
        const err = await res.json();
        alert(err.detail || "가입에 실패했습니다.");
    }
}

// 로그아웃
function logout() {
    accessToken = null;
    isAdmin = false;
    currentUsername = "";
    localStorage.removeItem("accessToken");
    localStorage.removeItem("isAdmin");
    localStorage.removeItem("username");
    unreadCounts = {};
    latestSeenFeedPostId = 0;
    latestKnownFeedPostId = 0;
    unseenFeedIds.clear();
    updateBottomNavBadges();
    updateUIByAuth();
    switchMobileView("feed");
}

// 로그아웃 버튼 핸들러
function handleLogout() {
    if (accessToken) {
        logout();
    } else {
        showAuthModal();
    }
}

// [v1.4.2 수정] 개발자 모드 진입 로직 수정 (토글 안정화)
function handleDevBtn() {
    const isDevView = document.body.classList.contains("is-dev");

    if (isDevView) {
        document.body.classList.remove("is-dev");
        document.getElementById("dev-btn").innerText = "DEV";
        switchMobileView("list");
    } else {
        if (isAdmin || document.body.classList.contains("dev-unlocked")) {
            document.body.classList.add("dev-unlocked", "is-dev");
            document.getElementById("dev-btn").innerText = "DEV OFF";
            switchMobileView("dev");
        } else {
            document.body.classList.add("show-auth");
        }
    }
}

function checkAuthPassword() {
    const input = document.getElementById("pass-input");
    if (input.value === DEV_PASS) {
        document.body.classList.remove("dev-locked", "show-auth");
        document.body.classList.add("dev-unlocked", "is-dev");
        document.getElementById("dev-btn").innerText = "DEV OFF";
        switchMobileView("dev");
        input.value = "";
    } else {
        alert("비밀번호가 틀렸습니다.");
        input.value = "";
    }
}

function closeAuth() {
    document.body.classList.remove("show-auth");
    document.getElementById("pass-input").value = "";
}

function _sumUnreadMessages() {
    return Object.values(unreadCounts || {}).reduce((acc, value) => {
        const n = Number(value) || 0;
        return acc + (n > 0 ? n : 0);
    }, 0);
}

function _setBadge(elId, count) {
    const el = document.getElementById(elId);
    if (!el) return;
    const n = Math.max(0, Number(count) || 0);
    if (n <= 0) {
        el.style.display = "none";
        return;
    }
    el.style.display = "inline-block";
    el.innerText = n > 99 ? "99+" : String(n);
}

function updateBottomNavBadges() {
    _setBadge("chats-tab-badge", _sumUnreadMessages());
    _setBadge("feed-tab-badge", unseenFeedIds.size);
}

function _getMaxFeedPostId(posts) {
    if (!Array.isArray(posts) || posts.length === 0) return 0;
    let maxId = 0;
    posts.forEach((post) => {
        const id = Number(post?.id || 0);
        if (Number.isFinite(id) && id > maxId) maxId = id;
    });
    return maxId;
}

function _ingestFeedPostsForBadge(posts, asSeen = false) {
    const maxId = _getMaxFeedPostId(posts);
    if (maxId > latestKnownFeedPostId) latestKnownFeedPostId = maxId;
    if (maxId <= 0) {
        updateBottomNavBadges();
        return;
    }

    if (asSeen || currentView === "feed") {
        latestSeenFeedPostId = Math.max(latestSeenFeedPostId, maxId);
        unseenFeedIds.clear();
        updateBottomNavBadges();
        return;
    }

    if (latestSeenFeedPostId <= 0) {
        latestSeenFeedPostId = maxId;
        updateBottomNavBadges();
        return;
    }

    posts.forEach((post) => {
        const id = Number(post?.id || 0);
        if (Number.isFinite(id) && id > latestSeenFeedPostId) {
            unseenFeedIds.add(id);
        }
    });
    updateBottomNavBadges();
}

function _markFeedSeen() {
    if (latestKnownFeedPostId > latestSeenFeedPostId) {
        latestSeenFeedPostId = latestKnownFeedPostId;
    }
    unseenFeedIds.clear();
    updateBottomNavBadges();
}

async function pollFeedBadge() {
    if (!accessToken || currentView === "feed") return;
    try {
        const res = await fetch("/api/feed?offset=0&limit=10", {
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        if (!res.ok) return;
        const data = await res.json();
        const posts = Array.isArray(data) ? data : (data.items || []);
        _ingestFeedPostsForBadge(posts, false);
    } catch (_) {
    }
}

function switchMobileView(view) {
    // [Phase 5] 게스트 모드 뷰 접근 제한
    if (!accessToken && ['map', 'list', 'chats', 'settings'].includes(view)) {
        showAuthModal();
        return;
    }

    currentView = view;
    document.body.classList.remove("is-chatting", "is-dev", "show-auth");
    const navs = document.querySelectorAll(".nav-item");
    navs.forEach((n) => n.classList.remove("active"));

    const title = document.getElementById("tab-title");
    const slider = document.getElementById("tab-slider");

    // [v2.0.0] Slider Logic Fix: display:none 제거하고 transform만 사용
    // 모든 탭을 flex로 유지해야 슬라이더 좌표계가 유지됨
    const feedTab = document.getElementById("feed-tab-view");
    const friendTab = document.getElementById("friend-tab-view");
    const chatTab = document.getElementById("chat-tab-view");
    const settingsTab = document.getElementById("settings-tab-view");
    const mapTab = document.getElementById("map-tab-view"); // [NEW]

    // 초기화 (혹시 inline style로 숨겨져 있을 경우 복구)
    if (feedTab) feedTab.style.display = "flex";
    if (friendTab) friendTab.style.display = "flex";
    if (chatTab) chatTab.style.display = "flex";
    if (settingsTab) settingsTab.style.display = "flex";
    if (mapTab) mapTab.style.display = "flex";

    // 탭 슬라이드 계산 (5개 탭 -> 500% width)
    // 0%, -20%, -40%, -60%, -80%
    if (view === "feed") {
        if (slider) slider.style.transform = "translateX(0%)";
        title.innerText = "LUXID";
        navs[0].classList.add("active");
        _markFeedSeen();
        loadFeed(true);
    } else if (view === "map") { // [NEW] 맵 탭
        if (slider) slider.style.transform = "translateX(-20%)";
        title.innerText = "WORLD";
        navs[1].classList.add("active");
        loadMap();
    } else if (view === "list") {
        if (slider) slider.style.transform = "translateX(-40%)";
        title.innerText = "친구";
        navs[2].classList.add("active");
        renderFriendList();
    } else if (view === "chats") {
        if (slider) slider.style.transform = "translateX(-60%)";
        title.innerText = "대화";
        navs[3].classList.add("active");
        renderChatList();
    } else if (view === "settings") {
        if (slider) slider.style.transform = "translateX(-80%)";
        title.innerText = "설정";
        navs[4].classList.add("active");
        loadSettings();
    } else if (view === "chat") {
        document.body.classList.add("is-chatting");
    } else if (view === "dev") {
        document.body.classList.add("is-dev");
        if (navs[4]) navs[4].classList.add("active");
        switchAdminTab("status");
    }
}

// [v1.4.2 수정] 관리자 상위 탭 전환 (3분할 탭 연동 + BATCH)
function switchAdminTab(tab) {
    const batchView = document.getElementById("admin-batch-view");
    const factoryView = document.getElementById("admin-factory-view");
    const views = {
        "status": document.getElementById("admin-status-view"),
        "users": document.getElementById("admin-user-view"),
        "notice": document.getElementById("admin-notice-view"),
        "batch": batchView || factoryView,
        "factory": factoryView || batchView,
        "custom": document.getElementById("admin-custom-view")
    };
    const detailView = document.getElementById("admin-detail-view");

    Object.values(views).forEach(v => { if (v) v.style.display = "none"; });
    if (detailView) detailView.style.display = "none";

    if (views[tab]) views[tab].style.display = "block";

    const atabStatus = document.getElementById("atab-status");
    const atabUsers = document.getElementById("atab-users");
    const atabNotice = document.getElementById("atab-notice");
    const atabBatch = document.getElementById("atab-batch");
    const atabFactory = document.getElementById("atab-factory");
    const atabCustom = document.getElementById("atab-custom");

    if (atabStatus) atabStatus.classList.toggle("active", tab === "status");
    if (atabUsers) atabUsers.classList.toggle("active", tab === "users");
    if (atabNotice) atabNotice.classList.toggle("active", tab === "notice");
    if (atabBatch) atabBatch.classList.toggle("active", tab === "batch" || tab === "factory");
    if (atabFactory) atabFactory.classList.toggle("active", tab === "factory" || tab === "batch");
    if (atabCustom) atabCustom.classList.toggle("active", tab === "custom");

    if (tab === "status") backToEveBrowser();
    if (tab === "users") loadAdminUsers();
}

// [v3.6.0] 이브 일괄 생성 로직 (Phase 1)
let batchPollInterval = null;

// 성별 슬라이더 업데이트
function updateGenderSlider(val) {
    const malePercent = 100 - parseInt(val);
    const femalePercent = parseInt(val);
    document.getElementById('batch-male-val').innerText = malePercent;
    document.getElementById('batch-female-val').innerText = femalePercent;
}

// 인종 가중치 연동 슬라이더 (하나 바꾸면 나머지 두 개가 비율 유지하며 조정)
function updateEthnicitySlider(changed, newVal) {
    newVal = parseInt(newVal);
    const others = { white: ['black', 'asian'], black: ['white', 'asian'], asian: ['white', 'black'] };
    const [a, b] = others[changed];
    const remaining = 100 - newVal;
    const aEl = document.getElementById(`batch-${a}`);
    const bEl = document.getElementById(`batch-${b}`);
    const aVal = parseInt(aEl.value);
    const bVal = parseInt(bEl.value);
    const total = aVal + bVal;

    let newA, newB;
    if (total === 0) {
        newA = Math.round(remaining / 2);
        newB = remaining - newA;
    } else {
        newA = Math.round((aVal / total) * remaining);
        newB = remaining - newA;
    }

    newA = Math.max(0, Math.min(100, newA));
    newB = Math.max(0, Math.min(100, newB));

    aEl.value = newA;
    bEl.value = newB;

    document.getElementById(`batch-${changed}-val`).innerText = newVal + '%';
    document.getElementById(`batch-${a}-val`).innerText = newA + '%';
    document.getElementById(`batch-${b}-val`).innerText = newB + '%';
}

function toggleBatchMultinational(enabled) {
    const wrap = document.getElementById("batch-ethnicity-wrap");
    if (wrap) wrap.style.display = enabled ? "block" : "none";
}

async function startBatchCreate() {
    const count = parseInt(document.getElementById("batch-count").value) || 1;
    const white = parseInt(document.getElementById("batch-white")?.value) || 33;
    const black = parseInt(document.getElementById("batch-black")?.value) || 33;
    const asian = parseInt(document.getElementById("batch-asian")?.value) || 34;
    const multinational = !!document.getElementById("batch-multinational")?.checked;
    // 여성 비율 (슬라이더 값 = 여성%)
    const femalePercent = parseInt(document.getElementById("batch-gender").value);

    const btn = document.getElementById("batch-create-btn");
    const progressContainer = document.getElementById("batch-progress-container");
    const progressBar = document.getElementById("batch-progress-bar");
    const progressText = document.getElementById("batch-progress-text");
    const logContainer = document.getElementById("batch-logs");

    btn.disabled = true;
    btn.style.opacity = 0.5;
    progressContainer.style.display = "block";
    progressBar.style.width = "0%";
    progressText.innerText = `0 / ${count} (0 Failed)`;
    if (logContainer) {
        logContainer.style.display = "none";
        logContainer.innerHTML = "";
    }

    try {
        const res = await fetch("/admin/batch-create-eves", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${accessToken}`
            },
            body: JSON.stringify({ count, white, black, asian, female_percent: femalePercent, multinational })
        });

        if (!res.ok) {
            let detail = "";
            try {
                const errData = await res.json();
                detail = errData?.detail ? ` (${errData.detail})` : "";
            } catch (_) { }
            throw new Error(`Batch start failed: ${res.status}${detail}`);
        }

        const data = await res.json();
        pollBatchStatus(data.job_id, count);
    } catch (e) {
        alert("일괄 생성 요청에 실패했습니다.");
        btn.disabled = false;
        btn.style.opacity = 1;
        progressContainer.style.display = "none";
    }
}

async function pollBatchStatus(jobId, totalCount) {
    if (batchPollInterval) clearInterval(batchPollInterval);

    const progressBar = document.getElementById("batch-progress-bar");
    const progressText = document.getElementById("batch-progress-text");
    const btn = document.getElementById("batch-create-btn");

    batchPollInterval = setInterval(async () => {
        try {
            const res = await fetch(`/admin/batch-status/${jobId}`, {
                headers: { "Authorization": `Bearer ${accessToken}` }
            });
            if (!res.ok) throw new Error();

            const data = await res.json();
            const percent = ((data.created + data.failed) / Math.max(data.total, 1)) * 100;

            progressBar.style.width = `${percent}%`;
            progressText.innerText = `${data.created} / ${data.total} (${data.failed} Failed)`;

            if (data.logs && data.logs.length > 0) {
                const logContainer = document.getElementById("batch-logs");
                if (logContainer) {
                    logContainer.style.display = "block";
                    logContainer.innerHTML = data.logs.map(lg => `<div style="margin-bottom:2px;">${lg}</div>`).join("");
                    logContainer.scrollTop = logContainer.scrollHeight;
                }
            }

            if (data.done) {
                clearInterval(batchPollInterval);
                batchPollInterval = null;
                btn.disabled = false;
                btn.style.opacity = 1;
                alert(`배치 생성이 완료되었습니다! (성공: ${data.created}, 실패: ${data.failed})`);
            }
        } catch (e) {
            console.error("Polling error", e);
        }
    }, 2000);
}

// [Phase 6] 추가: 페이지 새로고침 시 진행 중인 작업을 복구
async function checkActiveBatchJob() {
    try {
        const res = await fetch("/admin/active-batch-job", {
            headers: { "Authorization": `Bearer ${accessToken}` }
        });
        if (res.ok) {
            const data = await res.json();
            if (data.job_id && data.status && !data.status.done) {
                const btn = document.getElementById("batch-create-btn");
                const progressContainer = document.getElementById("batch-progress-container");
                if (btn) {
                    btn.disabled = true;
                    btn.style.opacity = 0.5;
                }
                if (progressContainer) progressContainer.style.display = "block";

                // 재연결 메시지 출력
                const logContainer = document.getElementById("batch-logs");
                if (logContainer) {
                    logContainer.style.display = "block";
                    logContainer.innerHTML += `<div style="color:var(--blue); margin-top:4px;">🔄 기존 작업에 재연결되었습니다...</div>`;
                }

                pollBatchStatus(data.job_id, data.status.total);
            }
        }
    } catch (e) {
        console.error("Failed to recover batch job", e);
    }
}

// [v1.4.2 신규] 관리자 STATUS 서브 탭 전환
function readOptionalJsonInput(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    const raw = (el.value || "").trim();
    if (!raw) return null;
    try {
        return JSON.parse(raw);
    } catch (e) {
        throw new Error(`${id} JSON format is invalid`);
    }
}

function collectCustomEvePayload() {
    const feedTimesRaw = (document.getElementById("custom-eve-feed-times")?.value || "").trim();
    const feedTimes = feedTimesRaw
        ? feedTimesRaw.split(",").map(v => v.trim()).filter(v => v)
        : null;

    return {
        name: (document.getElementById("custom-eve-name")?.value || "").trim() || null,
        age: (document.getElementById("custom-eve-age")?.value || "").trim() || null,
        gender: (document.getElementById("custom-eve-gender")?.value || "").trim() || null,
        mbti: (document.getElementById("custom-eve-mbti")?.value || "").trim() || null,
        p_seriousness: (document.getElementById("custom-p-seriousness")?.value || "").trim() || null,
        p_friendliness: (document.getElementById("custom-p-friendliness")?.value || "").trim() || null,
        p_rationality: (document.getElementById("custom-p-rationality")?.value || "").trim() || null,
        p_slang: (document.getElementById("custom-p-slang")?.value || "").trim() || null,
        image_prompt: (document.getElementById("custom-eve-image-prompt")?.value || "").trim() || null,
        face_base_url: (document.getElementById("custom-eve-base-image-url")?.value || "").trim() || null,
        profile_image_url: (document.getElementById("custom-eve-image-url")?.value || "").trim() || null,
        profile_details: readOptionalJsonInput("custom-eve-profile-details"),
        daily_schedule: readOptionalJsonInput("custom-eve-daily-schedule"),
        feed_times: feedTimes,
        v_likeability: (document.getElementById("custom-v-likeability")?.value || "").trim() || null,
        v_erotic: (document.getElementById("custom-v-erotic")?.value || "").trim() || null,
        v_v_mood: (document.getElementById("custom-v-mood")?.value || "").trim() || null,
        v_relationship: (document.getElementById("custom-v-relationship")?.value || "").trim() || null,
        generate_image: !!document.getElementById("custom-generate-image")?.checked
    };
}

function loadCustomImageFile(inputId, targetUrlId, previewId) {
    const fileInput = document.getElementById(inputId);
    const targetUrl = document.getElementById(targetUrlId);
    const preview = document.getElementById(previewId);
    if (!fileInput || !targetUrl) return;

    const file = fileInput.files && fileInput.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function (e) {
        const dataUrl = e.target?.result;
        if (!dataUrl) return;
        targetUrl.value = dataUrl;
        if (preview) {
            preview.src = dataUrl;
            preview.style.display = "block";
        }
    };
    reader.readAsDataURL(file);
}

function fillCustomEveForm(data) {
    if (!data) return;
    const setVal = (id, value) => {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null) el.value = value;
    };

    setVal("custom-eve-name", data.name);
    setVal("custom-eve-age", data.age);
    setVal("custom-eve-gender", data.gender);
    setVal("custom-eve-mbti", data.mbti);
    setVal("custom-p-seriousness", data.p_seriousness);
    setVal("custom-p-friendliness", data.p_friendliness);
    setVal("custom-p-rationality", data.p_rationality);
    setVal("custom-p-slang", data.p_slang);
    setVal("custom-eve-image-prompt", data.image_prompt);
    setVal("custom-eve-base-image-url", data.face_base_url);
    setVal("custom-eve-image-url", data.profile_image_url);
    setVal("custom-v-likeability", data.v_likeability);
    setVal("custom-v-erotic", data.v_erotic);
    setVal("custom-v-mood", data.v_v_mood);
    setVal("custom-v-relationship", data.v_relationship);
    if (Array.isArray(data.feed_times)) {
        setVal("custom-eve-feed-times", data.feed_times.join(","));
    }

    const pd = document.getElementById("custom-eve-profile-details");
    if (pd && data.profile_details) pd.value = JSON.stringify(data.profile_details, null, 2);
    const ds = document.getElementById("custom-eve-daily-schedule");
    if (ds && data.daily_schedule) ds.value = JSON.stringify(data.daily_schedule, null, 2);

    const basePreview = document.getElementById("custom-eve-base-image-preview");
    if (basePreview && data.face_base_url) {
        basePreview.src = data.face_base_url;
        basePreview.style.display = "block";
    }
    const profilePreview = document.getElementById("custom-eve-image-preview");
    if (profilePreview && data.profile_image_url) {
        profilePreview.src = data.profile_image_url;
        profilePreview.style.display = "block";
    }
}

async function autoFillCustomEve() {
    const statusEl = document.getElementById("custom-eve-status");
    const btn = document.getElementById("custom-autofill-btn");
    if (!btn) return;

    try {
        const payload = collectCustomEvePayload();
        btn.disabled = true;
        btn.style.opacity = 0.5;
        if (statusEl) statusEl.innerText = "AI가 빈칸을 채우는 중...";

        const res = await fetch("/admin/custom-eve/autofill", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${accessToken}`
            },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error("autofill failed");

        const data = await res.json();
        fillCustomEveForm(data.data || {});
        if (statusEl) statusEl.innerText = "AI 자동 채움 완료";
    } catch (e) {
        console.error(e);
        alert("빈칸 자동 채우기 실패");
        if (statusEl) statusEl.innerText = "자동 채움 실패";
    } finally {
        btn.disabled = false;
        btn.style.opacity = 1;
    }
}

async function createCustomEve() {
    const statusEl = document.getElementById("custom-eve-status");
    const btn = document.getElementById("custom-create-btn");
    if (!btn) return;

    try {
        const payload = collectCustomEvePayload();
        btn.disabled = true;
        btn.style.opacity = 0.5;
        if (statusEl) statusEl.innerText = "커스텀 이브 생성 중...";

        const res = await fetch("/admin/custom-eve/create", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${accessToken}`
            },
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            let msg = "create failed";
            try {
                const err = await res.json();
                msg = err.detail || err.message || msg;
            } catch (_) { }
            throw new Error(msg);
        }

        const created = await res.json();
        if (statusEl) statusEl.innerText = `생성 완료: ${created.name} (#${created.persona_id})`;

        await loadAdminEves();
        await loadFriends();
        switchAdminTab("status");
    } catch (e) {
        console.error(e);
        alert(`커스텀 이브 생성 실패: ${e.message || ""}`);
        if (statusEl) statusEl.innerText = `생성 실패: ${e.message || ""}`;
    } finally {
        btn.disabled = false;
        btn.style.opacity = 1;
    }
}

function switchAdminSubTab(sub) {
    const contents = document.querySelectorAll(".admin-sub-content");
    contents.forEach(c => c.style.display = "none");

    const target = document.getElementById(`asub-${sub}`);
    if (target) target.style.display = "flex";

    const tabs = document.querySelectorAll(".sub-atab");
    tabs.forEach(t => {
        t.classList.toggle("active", t.dataset.sub === sub);
    });

    if (sub === "identity") loadAdminIdentity();
    if (sub === "prompt") loadPromptTemplate();
    if (sub === "debug") loadDebugLogs();
    if (sub === "create") checkActiveBatchJob();
}

let adminEvesData = [];

// [v2.0.0] 모든 이브 목록(World EVE Browser) 로드 - 이브 중심
async function loadAdminEves() {
    const res = await fetch("/admin/eves", {
        headers: { Authorization: `Bearer ${accessToken}` }
    });
    if (res.ok) {
        adminEvesData = await res.json();
        renderAdminEves();
    }
}

// [Phase 6] 관리자 이브 목록 렌더링 (로컬 데이터 기반)
function renderAdminEves() {
    const listContainer = document.getElementById("admin-eve-tree-list");
    if (!listContainer) return;

    listContainer.innerHTML = `
        <div class="admin-eve-card-grid">
            ${adminEvesData.map(eve => {
        const isSelected = selectedAdminEves.has(eve.persona_id);
        return `
                <div class="admin-eve-card ${isAdminEveEditMode ? 'edit-mode' : ''} ${isSelected ? 'is-selected' : ''}" 
                     onclick="${isAdminEveEditMode ? `toggleAdminEveSelect(${eve.persona_id})` : ''}">
                    <div class="admin-eve-card-header">
                        ${isAdminEveEditMode ? `
                            <div class="admin-eve-check-wrap">
                                <div class="admin-eve-check ${isSelected ? 'checked' : ''}"></div>
                            </div>
                        ` : ''}
                        <div style="position:relative; flex-shrink:0;">
                            <img src="${eve.persona_image || 'https://via.placeholder.com/44'}" 
                                 class="admin-eve-card-avatar"
                                 onclick="if(!isAdminEveEditMode && '${eve.persona_image}') { event.stopPropagation(); openLightbox('${eve.persona_image}'); }"
                                 onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                            <div style="display:none; width:44px; height:44px; border-radius:50%; background:#2c2c2e; border:2px solid var(--blue); align-items:center; justify-content:center; font-size:20px;">🤖</div>
                        </div>
                        <div class="admin-eve-card-info">
                            <div class="admin-eve-card-name" style="cursor:pointer; text-decoration:underline; text-underline-offset:3px;" 
                                 onclick="${isAdminEveEditMode ? '' : `event.stopPropagation(); openAdminPersonaProfile(${eve.persona_id})`}">${eve.persona_name}</div>
                            <div class="admin-eve-card-stat">${eve.rooms.length} 연결된 유저</div>
                        </div>
                    </div>
                    <div class="admin-eve-card-body">
                        ${eve.rooms.map(room => `
                            <div class="admin-eve-room-item" onclick="${isAdminEveEditMode ? '' : `event.stopPropagation(); inspectEve(${room.room_id}, '${eve.persona_name} - ${room.user_name}')`}">
                                <div class="admin-eve-room-user">${room.user_name}</div>
                                <div class="admin-eve-room-status ${room.is_active ? 'live' : 'idle'}">
                                    ${room.is_active ? '● LIVE' : '○ IDLE'}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `}).join('')}
        </div>
    `;
}

// [v3.3.0 추가] 관리자 탭에서 이브 전체 프로필 열기
async function openAdminPersonaProfile(personaId) {
    if (!accessToken) return;

    try {
        const res = await fetch(`/admin/persona/${personaId}/details`, {
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        if (!res.ok) throw new Error("Failed to load details");

        const details = await res.json();
        const p = adminEvesData.find(item => item.persona_id === personaId) || {};

        // 1. Populate visual details
        document.getElementById("admin-detail-name").innerText = `${details.name} 상세 정보`;

        const baseImg = document.getElementById("admin-detail-base-img");
        baseImg.src = details.face_base_url || "";
        baseImg.style.display = details.face_base_url ? "block" : "none";

        const facePromptEl = document.getElementById("admin-detail-face-prompt");
        if (facePromptEl) {
            facePromptEl.textContent = details.face_prompt || "";
        }

        const avatarImg = document.getElementById("admin-detail-avatar-img");
        avatarImg.src = p.persona_image || "";
        avatarImg.style.display = p.persona_image ? "block" : "none";

        // 2. Feed Activities
        const feedsContainer = document.getElementById("admin-detail-feeds");
        if (details.feed_posts && details.feed_posts.length > 0) {
            feedsContainer.innerHTML = details.feed_posts.map(f => `
                <div style="padding:12px; background:#f9f9f9; border-radius:8px; border:1px solid var(--border);">
                    <div style="font-size:11px; color:var(--text-sub); margin-bottom:4px;">${f.created_at}</div>
                    <div style="font-size:13px; color:var(--text-main); margin-bottom:${f.image_url ? '8px' : '0'}; line-height:1.4;">${f.content}</div>
                    ${f.image_url ? `<img src="${f.image_url}" style="width:100%; max-height:120px; object-fit:cover; border-radius:6px; cursor:pointer;" onclick="openLightbox('${f.image_url}')">` : ''}
                </div>
            `).join("");
        } else {
            feedsContainer.innerHTML = '<div style="font-size:13px; color:var(--text-sub);">피드 활동 없음</div>';
        }

        // 3. Social Graph
        const friendsContainer = document.getElementById("admin-detail-friends");
        if (details.friends && details.friends.length > 0) {
            friendsContainer.innerHTML = details.friends.map(fr => `
                <div style="padding:6px 12px; background:${fr.type === 'EVE' ? '#e3f2fd' : '#f3e5f5'}; border-radius:16px; font-size:12px; font-weight:600; color:var(--text-main); display:inline-flex; align-items:center; gap:6px; border:1px solid ${fr.type === 'EVE' ? '#bbdefb' : '#e1bee7'};">
                    <span style="font-size:14px;">${fr.type === 'EVE' ? '🤖' : '👤'}</span>
                    <span>${fr.name}</span>
                    <span style="background:rgba(0,0,0,0.1); padding:2px 6px; border-radius:8px; font-size:10px;">${fr.relationship} ${fr.interactions !== '-' ? `(${fr.interactions})` : ''}</span>
                </div>
            `).join("");
        } else {
            friendsContainer.innerHTML = '<div style="font-size:13px; color:var(--text-sub);">친구 없음</div>';
        }

        // 4. Conversations & Memories
        const memContainer = document.getElementById("admin-detail-memories");
        let memHtml = '';

        if (details.conversations && details.conversations.length > 0) {
            memHtml += `<div style="font-size:14px; font-weight:bold; margin-bottom:6px; color:var(--text-main);">[ 최근 이브 간 대화 요약 ]</div>`;
            memHtml += details.conversations.map(c => `
                <div style="padding:10px 14px; background:#fff8e1; border-radius:8px; font-size:13px; border:1px solid #ffecb3; margin-bottom:8px; line-height:1.5; color:var(--text-main);">
                    <div style="font-weight:bold; color:#f57f17; margin-bottom:4px;">with ${c.with}</div>
                    ${c.summary}
                </div>
            `).join("");
        }

        if (details.shared_memory && details.shared_memory.length > 0) {
            memHtml += `<div style="font-size:14px; font-weight:bold; margin-top:12px; margin-bottom:6px; color:var(--text-main);">[ 저장된 주요 기억 (Memories) ]</div>`;
            memHtml += details.shared_memory.slice(-10).reverse().map(m => `
                <div style="padding:8px 12px; background:#f0f4c3; border-radius:8px; font-size:13px; border:1px solid #e6ee9c; margin-bottom:6px; line-height:1.4; color:var(--text-main);">
                    <span style="font-size:10px; color:#9e9d24; display:block; margin-bottom:2px;">[${m.category || '기억'}] ${m.ts || ''}</span>
                    ${m.fact}
                </div>
            `).join("");
        }

        if (!memHtml) {
            memHtml = '<div style="font-size:13px; color:var(--text-sub);">기억/대화 내역 없음</div>';
        }
        memContainer.innerHTML = memHtml;

        // Show modal
        document.getElementById("admin-eve-detail-modal").style.display = "flex";

    } catch (err) {
        console.error("openAdminPersonaProfile err:", err);
        alert("이브 세부 정보를 불러오는데 실패했습니다.");
    }
}

// [v1.4.2 신규] 특정 이브 제어 모드 진입
async function inspectEve(roomId, name) {
    adminSelectedRoomId = roomId;
    document.getElementById("admin-eve-browser").style.display = "none";
    document.getElementById("admin-eve-control").style.display = "block";
    document.getElementById("admin-controlled-eve-name").innerText = `${name} (ID: ${roomId})`;

    switchAdminSubTab("engine");
    await fetchAndSyncVolatile(roomId);
}

async function fetchAndSyncVolatile(roomId) {
    const res = await fetch(`/admin/room/${roomId}/volatile`, {
        headers: { Authorization: `Bearer ${accessToken}` }
    });
    if (res.ok) {
        const data = await res.json();
        // UI 싱크를 위해 handleIncomingData와 동일한 처리 흐름 사용
        handleIncomingData(roomId, { current_status: data });
    }
}

// [v1.4.2 신규] 목록으로 돌아가기
function backToEveBrowser() {
    adminSelectedRoomId = null;
    const browser = document.getElementById("admin-eve-browser");
    const control = document.getElementById("admin-eve-control");
    if (browser) browser.style.display = "block";
    if (control) control.style.display = "none";
    loadAdminEves();
}

async function loadFriends() {
    try {
        const res = await fetch("/friends", {
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (res.status === 401) return logout();
        friendsData = await res.json();
        const validRoomIds = new Set((friendsData || []).map((f) => Number(f.room_id)));
        Object.keys(unreadCounts || {}).forEach((k) => {
            const rid = Number(k);
            if (!validRoomIds.has(rid)) delete unreadCounts[k];
        });
        renderFriendList();
        renderChatList();
        updateBottomNavBadges();
    } catch (e) {
        setEngineStatus(false);
    }
}

function renderFriendList() {
    const list = document.getElementById("friend-list");
    if (!list) return;

    // Update header state
    const editToggleBtn = document.getElementById("friend-edit-toggle-btn");
    const editHeader = document.getElementById("friend-edit-header");
    const bulkBar = document.getElementById("friend-bulk-bar");
    const countLabel = document.getElementById("friend-count-label");
    const addBtn = document.getElementById("add-friend-btn");

    if (friendsData.length === 0) {
        if (editHeader) editHeader.style.display = "none";
        if (bulkBar) bulkBar.style.display = "none";
        isEditMode = false;
        selectedFriendRooms.clear();
        if (addBtn) addBtn.style.display = "";
        list.innerHTML = `
            <div class="empty-state" style="margin-top: 20px;">
                <div class="empty-icon" style="font-size: 40px; margin-bottom: 10px;">👋</div>
                <div class="empty-text" style="font-size: 16px; font-weight: bold; color: var(--text-main);">아직 친구가 없네요!</div>
                <div style="font-size: 13px; color: var(--text-sub); margin-top: 5px;">새로운 이브들을 만나보세요.</div>
            </div>
            
            <!-- [Phase 5] Suggested Friends Carousel -->
            <div class="suggested-section" style="margin-top: 30px; padding: 0 20px;">
                <div style="font-size: 14px; font-weight: 800; color: var(--text-main); margin-bottom: 15px;">추천 친구</div>
                <div id="suggested-carousel" style="display: flex; gap: 15px; overflow-x: auto; padding-bottom: 20px; scroll-snap-type: x mandatory;">
                    <div class="skeleton-box" style="width: 120px; height: 160px; flex-shrink: 0; border-radius: 16px;"></div>
                    <div class="skeleton-box" style="width: 120px; height: 160px; flex-shrink: 0; border-radius: 16px;"></div>
                    <div class="skeleton-box" style="width: 120px; height: 160px; flex-shrink: 0; border-radius: 16px;"></div>
                </div>
            </div>
        `;
        loadSuggestedFriends();
        return;
    }

    // Show/hide edit UI elements based on mode
    if (editHeader) editHeader.style.display = "flex";
    if (addBtn) addBtn.style.display = isEditMode ? "none" : "";
    if (editToggleBtn) editToggleBtn.innerText = isEditMode ? "완료" : "편집";
    if (countLabel) countLabel.innerText = `${friendsData.length}명의 이브`;

    if (bulkBar) {
        bulkBar.style.display = isEditMode ? "flex" : "none";
        const selCount = document.getElementById("friend-selected-count");
        if (selCount) selCount.innerText = `${selectedFriendRooms.size}개 선택됨`;
    }

    list.innerHTML = friendsData
        .map((f) => {
            const colors = ["#FF9500", "#FF2D55", "#AF52DE", "#5AC8FA", "#34C759"];
            const color = colors[f.name.charCodeAt(0) % 5];
            const isOnline = f.status === "online";
            const avatarContent = f.profile_image_url
                ? `<img src="${f.profile_image_url}" class="avatar-img">`
                : f.name[0];

            let statusActivity = "LUXID 체류 중";
            if (isOnline) statusActivity = "PORTAL 접속 중";

            if (f.daily_schedule) {
                const hour = new Date().getHours();
                if (typeof f.daily_schedule === 'object' && !Array.isArray(f.daily_schedule)) {
                    const wake = parseInt((f.daily_schedule.wake_time || "07:00").split(":")[0]);
                    const sleep = parseInt((f.daily_schedule.sleep_time || "23:00").split(":")[0]);
                    if (hour < wake || hour >= sleep) statusActivity = "휴식 중";
                    else statusActivity = "활동 중";
                } else if (Array.isArray(f.daily_schedule)) {
                    const nowActivity = f.daily_schedule.find(s => {
                        if (!s.time || !s.time.includes("-")) return false;
                        const times = s.time.split("-");
                        const startH = parseInt(times[0].split(":")[0]);
                        const endH = parseInt(times[1].split(":")[0]);
                        return hour >= startH && hour < endH;
                    });
                    if (nowActivity) statusActivity = nowActivity.activity;
                }
            }

            const isSelected = selectedFriendRooms.has(f.room_id);
            const clickAction = isEditMode
                ? `toggleFriendSelect(${f.room_id})`
                : `openProfile(${f.room_id})`;

            return `
            <div class="friend-item portal-card ${isOnline ? "is-online" : ""} ${isEditMode ? "edit-mode" : ""} ${isSelected ? "is-selected" : ""}" onclick="${clickAction}">
                ${isEditMode ? `<div class="friend-check-wrap"><div class="friend-check ${isSelected ? "checked" : ""}"></div></div>` : ""}
                <div class="friend-avatar" style="background:${f.profile_image_url ? "none" : color}">
                    ${avatarContent}
                    <div class="online-dot"></div>
                </div>
                <div class="friend-info">
                    <div class="name-row"><span class="name">${f.name}</span><span class="mbti">${f.mbti}</span></div>
                    <div class="last-msg">${statusActivity}</div>
                </div>
                ${!isEditMode ? `<button class="friend-delete-quick" onclick="event.stopPropagation(); deleteFriend(event, ${f.room_id})" title="친구 삭제">×</button>` : ""}
            </div>
        `;
        })
        .join("");
}

function renderChatList() {
    const list = document.getElementById("chat-list");
    if (!list) return;
    const chattingFriends = friendsData.filter((f) => f.history && f.history.length > 0);

    if (chattingFriends.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">💬</div>
                <div class="empty-text">대화가 없습니다</div>
            </div>
        `;
        return;
    }

    list.innerHTML = chattingFriends
        .map((f) => {
            const lastMsg = f.history[f.history.length - 1].content;
            const colors = ["#FF9500", "#FF2D55", "#AF52DE", "#5AC8FA", "#34C759"];
            const color = colors[f.name.charCodeAt(0) % 5];
            const unread = unreadCounts[f.room_id] || 0;
            const avatarContent = f.profile_image_url
                ? `<img src="${f.profile_image_url}" class="avatar-img">`
                : f.name[0];

            return `
            <div class="friend-item ${unread > 0 ? "has-unread" : ""}" onclick="joinRoom(${f.room_id})">
                <div class="friend-avatar" style="background:${f.profile_image_url ? "none" : color}">${avatarContent}</div>
                <div class="friend-info">
                    <div class="name-row"><span class="name">${f.name}</span></div>
                    <div class="last-msg">${lastMsg}</div>
                </div>
                <div class="unread-badge">${unread}</div>
                <button class="delete-btn" onclick="deleteFriend(event, ${f.room_id})">×</button>
            </div>
        `;
        })
        .join("");
}

// [Phase 5] Mini Profile Bottom Sheet Logic
let currentMiniProfileUser = null;

async function openMiniProfile(authorId, roomId) {
    currentMiniProfileUser = { authorId, roomId };

    // Fetch persona details
    try {
        const res = await fetch(`/api/public/persona/${authorId}`);
        if (!res.ok) throw new Error("Failed to fetch persona");
        const data = await res.json();

        document.getElementById('mp-avatar').src = data.profile_image_url || '';
        document.getElementById('mp-name').textContent = data.name;
        document.getElementById('mp-intro').textContent = data.intro || '자기소개가 없습니다.';

        // Update button state based on friendship
        const addBtn = document.getElementById("mp-btn-add");
        if (roomId) {
            addBtn.textContent = "이미 친구";
            addBtn.style.background = "#E5E5EA";
            addBtn.style.color = "#000";
            addBtn.disabled = false;
        } else {
            addBtn.textContent = "친구 추가";
            addBtn.style.background = "#E5E5EA";
            addBtn.style.color = "#000";
            addBtn.disabled = false;
        }

        // Show overlay with animation
        const overlay = document.getElementById('mini-profile-overlay');
        overlay.style.display = 'flex';

    } catch (e) {
        console.error("Error loading mini profile:", e);
    }
}

function closeMiniProfile() {
    document.getElementById('mini-profile-overlay').style.display = 'none';
    currentMiniProfileUser = null;
}

// "친구 추가" 버튼
async function handleAddFriendFromMP() {
    if (!accessToken) return showAuthModal();
    if (!currentMiniProfileUser) return;
    if (currentMiniProfileUser.roomId) {
        alert("이미 추가되어 있습니다.");
        return;
    }

    try {
        const res = await fetch(`/api/friends/${currentMiniProfileUser.authorId}/add`, {
            method: "POST",
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        const data = await res.json();
        if (data.status === "success") {
            // Update current user so DM starts working immediately
            currentMiniProfileUser.roomId = data.room_id;
            const addBtn = document.getElementById("mp-btn-add");
            addBtn.textContent = "이미 친구";
            addBtn.style.background = "#E5E5EA";
            addBtn.style.color = "#000";
            addBtn.disabled = false;
            if (String(data.message || "").includes("이미 친구")) {
                alert("이미 추가되어 있습니다.");
            } else {
                alert("친구탭에 추가되었습니다.");
            }

            // Reload friends list in background
            loadFriends();
        }
    } catch (e) {
        console.error("Add friend failed", e);
        alert("친구 추가에 실패했습니다.");
    }
}

// "DM 보내기" 버튼
function handleDMFromMP() {
    if (!accessToken) return showAuthModal();
    if (!currentMiniProfileUser) return;

    closeMiniProfile();
    if (currentMiniProfileUser.roomId) {
        // [Phase 5] DM 확인창 없이 바로 채팅방 진입 (UX 개선)
        joinRoom(currentMiniProfileUser.roomId);
    } else {
        alert("먼저 친구를 추가해야 DM을 보낼 수 있습니다.");
    }
}

// [Phase 5] 추천 친구 캐러셀 로드
async function loadSuggestedFriends() {
    try {
        const res = await fetch('/api/public/personas/suggested?limit=5', {
            headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
        });
        const suggested = await res.json();
        const carousel = document.getElementById("suggested-carousel");

        if (!carousel) return;

        carousel.innerHTML = suggested.map(p => `
            <div class="suggested-card" onclick="openMiniProfile(${p.id}, null)" style="width: 140px; min-height: 180px; flex-shrink: 0; background: #fff; border-radius: 16px; border: 1px solid var(--border); overflow: hidden; display: flex; flex-direction: column; align-items: center; padding: 15px; text-align: center; cursor: pointer; transition: transform 0.2s; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
                <img src="${p.profile_image_url || 'https://via.placeholder.com/60'}" style="width: 60px; height: 60px; border-radius: 50%; object-fit: cover; margin-bottom: 10px;">
                <div style="font-weight: 800; font-size: 14px; color: var(--text-main); margin-bottom: 4px;">${p.name}</div>
                <div style="font-size: 11px; color: var(--text-sub); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${p.intro}</div>
                <button class="trendy-btn" style="width: 100%; margin-top: auto; padding: 8px !important; font-size: 12px !important; border-radius: 8px !important;" onclick="event.stopPropagation(); openMiniProfile(${p.id}, null)">프로필 보기</button>
            </div>
        `).join('');
    } catch (e) {
        console.error("Failed to load suggested friends:", e);
    }
}

function renderFullProfile(data) {
    const heroImg = document.getElementById("profile-hero-image");
    const photoStrip = document.getElementById("fp-photo-strip");
    const name = document.getElementById("fp-name");
    const mbti = document.getElementById("fp-mbti");
    const hook = document.getElementById("fp-hook");
    const hookCard = document.getElementById("fp-hook-card");
    const age = document.getElementById("fp-age");
    const gender = document.getElementById("fp-gender");
    const statFriends = document.getElementById("fp-stat-friends");
    const statActive = document.getElementById("fp-stat-active");
    const statRel = document.getElementById("fp-stat-rel");
    const personalitySection = document.getElementById("fp-personality-section");
    const vibeSection = document.getElementById("fp-vibe-section");
    const diarySection = document.getElementById("fp-diary-section");
    const diaryList = document.getElementById("fp-diary-list");
    const details = data.profile_details || {};

    const hookText = (details.hook || "").trim() || "No hook yet.";
    const ageText = Number.isFinite(Number(data.age)) ? `${data.age}y` : "-y";
    const genderText = data.gender || "-";

    const rawGallery = Array.isArray(data.profile_images) ? data.profile_images : [];
    const gallery = rawGallery
        .map((item) => (item && typeof item === "object") ? (item.url || "") : String(item || ""))
        .map((u) => String(u || "").trim())
        .filter((u, idx, arr) => !!u && arr.indexOf(u) === idx);
    if (data.profile_image_url && !gallery.includes(data.profile_image_url)) {
        gallery.unshift(data.profile_image_url);
    }

    const applyHeroImage = (url) => {
        heroImg.style.backgroundImage = `url('${url}')`;
        heroImg.style.background = `url('${url}') center/cover no-repeat`;
        heroImg.classList.add("clickable");
        heroImg.onclick = (e) => {
            if (e.target.closest(".profile-hero-back")) return;
            openLightbox(url, data.image_prompt || "");
        };
    };

    if (gallery.length > 0) {
        applyHeroImage(gallery[0]);
        if (photoStrip) {
            photoStrip.style.display = gallery.length > 1 ? "flex" : "none";
            photoStrip.innerHTML = "";
            gallery.forEach((url, idx) => {
                const img = document.createElement("img");
                img.className = `fp-photo-thumb ${idx === 0 ? "active" : ""}`;
                img.src = url;
                img.alt = "photo";
                img.addEventListener("click", () => {
                    applyHeroImage(url);
                    photoStrip.querySelectorAll(".fp-photo-thumb").forEach((el) => el.classList.remove("active"));
                    img.classList.add("active");
                });
                photoStrip.appendChild(img);
            });
        }
    } else {
        const colors = ["#E05555", "#E07A3B", "#2F7EA5", "#2E8B57", "#865CC7"];
        const idx = (data.name || "E").charCodeAt(0) % colors.length;
        heroImg.style.backgroundImage = "none";
        heroImg.style.background = `linear-gradient(135deg, ${colors[idx]}, #111827)`;
        heroImg.classList.remove("clickable");
        heroImg.onclick = null;
        if (photoStrip) {
            photoStrip.style.display = "none";
            photoStrip.innerHTML = "";
        }
    }

    name.innerText = data.name || "EVE";
    mbti.innerText = data.mbti || "-";
    hook.innerText = hookText;
    hookCard.innerText = hookText;
    age.innerText = ageText;
    gender.innerText = genderText;
    statRel.innerText = data.relationship_category || "Unknown";

    const asScore = (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return "-";
        return `${n}/10`;
    };
    const asPercent = (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return "-";
        return `${Math.max(0, Math.min(100, Math.round(n)))}%`;
    };
    const esc = (v) => String(v || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");

    if (personalitySection) personalitySection.style.display = isAdmin ? "block" : "none";
    if (vibeSection) vibeSection.style.display = isAdmin ? "block" : "none";
    if (diarySection && diaryList) {
        const diaries = Array.isArray(data.diaries) ? data.diaries : [];
        if (isAdmin) {
            diarySection.style.display = "block";
            if (diaries.length > 0) {
                const ordered = [...diaries].reverse();
                diaryList.innerHTML = ordered.map((d) => `
                    <div style="background:var(--bg-secondary); border-radius:10px; padding:12px; font-size:13px; line-height:1.6;">
                        <div style="color:var(--text-sub); font-size:11px; margin-bottom:6px;">${esc(d.date || "")}</div>
                        <div style="color:var(--text-main); white-space:pre-wrap;">${esc(d.content || "")}</div>
                    </div>
                `).join("");
            } else {
                diaryList.innerHTML = `<div style="color:var(--text-sub); font-size:13px;">일기가 없습니다.</div>`;
            }
        } else {
            diarySection.style.display = "none";
            diaryList.innerHTML = "";
        }
    }

    document.getElementById("fp-trait-serious").innerText = asScore(data.p_seriousness);
    document.getElementById("fp-trait-friendly").innerText = asScore(data.p_friendliness);
    document.getElementById("fp-trait-rational").innerText = asScore(data.p_rationality);
    document.getElementById("fp-trait-slang").innerText = asScore(data.p_slang);

    document.getElementById("fp-vibe-like").innerText = asPercent(data.v_likeability);
    document.getElementById("fp-vibe-mood").innerText = asPercent(data.v_v_mood);
    document.getElementById("fp-vibe-rel").innerText = Number.isFinite(Number(data.v_relationship)) ? `${Math.round(Number(data.v_relationship))}` : "-";
    document.getElementById("fp-vibe-erotic").innerText = asPercent(data.v_erotic);

    if (statFriends) statFriends.innerText = "0";
    if (statActive) statActive.innerText = "0";
    if (data.persona_id) {
        fetch(`/persona/${data.persona_id}/stats`, {
            headers: { Authorization: `Bearer ${accessToken}` }
        }).then(r => r.ok ? r.json() : null).then(stats => {
            if (stats) {
                if (statFriends) statFriends.innerText = stats.total_friends;
                if (statActive) statActive.innerText = stats.active_chats_1h;

                const feedSection = document.getElementById('fp-feed-section');
                const feedList = document.getElementById('fp-feed-list');
                if (feedSection && feedList && stats.feed_posts && stats.feed_posts.length > 0) {
                    feedList.innerHTML = stats.feed_posts.map(p => `
                        <div style="background:var(--bg-secondary); border-radius:10px; padding:12px; font-size:13px; line-height:1.5;">
                            <div style="color:var(--text-sub); font-size:11px; margin-bottom:4px;">${p.created_at}</div>
                            <div style="color:var(--text-main);">${p.content}</div>
                            ${p.image_url ? `<img src="${p.image_url}" style="width:100%; max-height:140px; object-fit:cover; border-radius:8px; margin-top:8px; cursor:pointer;" onclick="openLightbox('${p.image_url}')">` : ''}
                        </div>
                    `).join('');
                    feedSection.style.display = 'block';
                } else if (feedSection) {
                    feedSection.style.display = 'none';
                }
            }
        }).catch(() => { });
    }
}

function findFriendByRoomId(roomId) {
    const target = Number(roomId);
    if (!Number.isFinite(target)) return null;
    return friendsData.find((item) => Number(item.room_id) === target) || null;
}

async function openProfile(roomId) {
    let f = findFriendByRoomId(roomId);
    if (!f && accessToken) {
        try {
            const res = await fetch("/friends", {
                headers: { Authorization: `Bearer ${accessToken}` }
            });
            if (res.ok) {
                friendsData = await res.json();
                f = findFriendByRoomId(roomId);
            }
        } catch (_) { }
    }
    if (!f) return;

    renderFullProfile(f);

    const chatBtn = document.getElementById("fp-chat-btn");
    const delBtn = document.getElementById("fp-delete-btn");
    const relBtn = document.getElementById("fp-rel-btn");
    const lifeBtn = document.getElementById("fp-life-btn");

    if (delBtn) delBtn.style.display = "";
    if (relBtn) relBtn.style.display = "";
    if (chatBtn) {
        chatBtn.innerText = "DM";
        chatBtn.onclick = () => {
            closeProfile();
            joinRoom(roomId);
        };
    }

    if (delBtn) {
        delBtn.onclick = (e) => {
            closeProfile();
            deleteFriend(e, roomId);
        };
    }

    if (relBtn) {
        relBtn.onclick = () => openRelationshipView(roomId);
    }

    if (lifeBtn) {
        if (isAdmin) {
            lifeBtn.style.display = "block";
            lifeBtn.onclick = () => openLifeDetails(roomId);
        } else {
            lifeBtn.style.display = "none";
        }
    }

    document.body.classList.add("is-profile");
    const profilePage = document.getElementById("profile-page");
    if (profilePage) profilePage.scrollTop = 0;
}

function closeProfile() {
    document.body.classList.remove("is-profile");
}

// [v1.7.0] 이미지 라이트박스 제어
function openLightbox(url, promptText = null) {
    const overlay = document.getElementById("lightbox-overlay");
    const img = document.getElementById("lightbox-img");
    const promptEl = document.getElementById("lightbox-prompt");
    let safePrompt = promptText || "";
    if (safePrompt) {
        try {
            safePrompt = decodeURIComponent(safePrompt);
        } catch (_) {
            // Keep raw text if it's not URI-encoded.
        }
    }
    if (overlay && img) {
        img.src = url;

        if (isAdmin && safePrompt) {
            promptEl.innerText = `[IMAGE PROMPT]\n${safePrompt}`;
            promptEl.style.display = "block";
        } else {
            promptEl.style.display = "none";
        }

        overlay.style.display = "flex";
    }
}

function closeLightbox() {
    const overlay = document.getElementById("lightbox-overlay");
    if (overlay) overlay.style.display = "none";
}

// [v1.7.0] 관계 분석 뷰 제어
function openRelationshipView(roomId) {
    const f = friendsData.find(item => item.room_id === roomId);
    if (!f) return;

    const overlay = document.getElementById("relationship-overlay");
    const badge = document.getElementById("rel-category-badge");

    // 카테고리 설정
    const category = f.relationship_category || "분석 중...";
    if (badge) badge.innerText = category;

    // 수치 애니메이션 바
    setBarValue("bar-likeability", "val-likeability", f.v_likeability || 0, "%");
    setBarValue("bar-relationship", "val-relationship", f.v_relationship || 0, ""); // 친밀도는 퍼센트 아님 (absolute logic) but display stats
    setBarValue("bar-erotic", "val-erotic", f.v_erotic || 0, "%");
    setBarValue("bar-mood", "val-mood", f.v_v_mood || 0, "%");

    if (overlay) overlay.style.display = "flex";
}

function setBarValue(barId, valId, value, unit) {
    const bar = document.getElementById(barId);
    const val = document.getElementById(valId);
    if (bar) bar.style.width = `${Math.min(100, Math.max(0, value))}%`;
    if (val) val.innerText = `${value}${unit}`;
}

function closeRelationshipView() {
    const overlay = document.getElementById("relationship-overlay");
    if (overlay) overlay.style.display = "none";
}

// [v1.7.1] Life Details 뷰 제어 (관리자 전용)
function openLifeDetails(roomId) {
    const f = friendsData.find(item => item.room_id === roomId);
    if (!f) return;

    const overlay = document.getElementById("life-details-overlay");

    // [v1.7.2] 현재 활동 계산 (신규/구형식 모두 지원)
    const currentActivity = document.getElementById("current-activity");
    if (f.daily_schedule) {
        const hour = new Date().getHours();

        // 신규 형식: {wake_time, daily_tasks, sleep_time}
        if (typeof f.daily_schedule === 'object' && !Array.isArray(f.daily_schedule)) {
            const wake = parseInt((f.daily_schedule.wake_time || "07:00").split(":")[0]);
            const sleep = parseInt((f.daily_schedule.sleep_time || "23:00").split(":")[0]);

            if (hour < wake || hour >= sleep) {
                currentActivity.innerText = "휴식 중";
            } else {
                const tasks = f.daily_schedule.daily_tasks || [];
                currentActivity.innerText = tasks.length > 0 ? tasks.join(", ") : "활동 중";
            }
        }
        // 구형식: [{time, activity}, ...]
        else if (Array.isArray(f.daily_schedule)) {
            const nowActivity = f.daily_schedule.find(s => {
                if (!s.time || !s.time.includes("-")) return false;
                const times = s.time.split("-");
                const startH = parseInt(times[0].split(":")[0]);
                const endH = parseInt(times[1].split(":")[0]);
                return hour >= startH && hour < endH;
            });
            currentActivity.innerText = nowActivity ? nowActivity.activity : "일정 없음";
        }
    } else {
        if (currentActivity) currentActivity.innerText = "일정 정보 없음";
    }

    // [v1.7.2] 오늘의 일정 렌더링 (신규/구형식 모두 지원)
    const scheduleContainer = document.getElementById("today-schedule");
    if (f.daily_schedule) {
        // 신규 형식: {wake_time, daily_tasks, sleep_time}
        if (typeof f.daily_schedule === 'object' && !Array.isArray(f.daily_schedule)) {
            const wake = f.daily_schedule.wake_time || "정보 없음";
            const sleep = f.daily_schedule.sleep_time || "정보 없음";
            const tasks = f.daily_schedule.daily_tasks || [];

            scheduleContainer.innerHTML = `
                <div style="padding: 8px 12px; background: var(--bg-secondary); border-radius: 8px; font-size: 12px;">
                    <span style="color: var(--accent); font-weight: 600;">기상</span>
                    <span style="color: var(--text-sub); margin-left: 8px;">${wake}</span>
                </div>
                ${tasks.map(task => `
                    <div style="padding: 8px 12px; background: var(--bg-secondary); border-radius: 8px; font-size: 12px;">
                        <span style="color: var(--text-sub);">• ${task}</span>
                    </div>
                `).join('')}
                <div style="padding: 8px 12px; background: var(--bg-secondary); border-radius: 8px; font-size: 12px;">
                    <span style="color: var(--accent); font-weight: 600;">취침</span>
                    <span style="color: var(--text-sub); margin-left: 8px;">${sleep}</span>
                </div>
            `;
        }
        // 구형식: [{time, activity}, ...]
        else if (Array.isArray(f.daily_schedule)) {
            scheduleContainer.innerHTML = f.daily_schedule.map(s => {
                return `<div style="padding: 8px 12px; background: var(--bg-secondary); border-radius: 8px; font-size: 12px;">
                    <span style="color: var(--accent); font-weight: 600;">${s.time}</span>
                    <span style="color: var(--text-sub); margin-left: 8px;">${s.activity}</span>
                </div>`;
            }).join("");
        }
    } else {
        scheduleContainer.innerHTML = `<div style="color: var(--text-sub); font-size: 13px;">일정이 없습니다.</div>`;
    }

    // 최근 일기 렌더링
    const diaryContainer = document.getElementById("recent-diary");
    if (f.diaries && Array.isArray(f.diaries) && f.diaries.length > 0) {
        const latestDiary = f.diaries[f.diaries.length - 1];
        diaryContainer.innerHTML = `
            <div style="padding: 12px; background: var(--bg-secondary); border-radius: 8px;">
                <div style="font-size: 11px; color: var(--text-sub); margin-bottom: 6px;">${latestDiary.date}</div>
                <div style="font-size: 13px; color: var(--text-main); line-height: 1.6;">${latestDiary.content}</div>
            </div>
        `;
    } else {
        diaryContainer.innerText = "일기가 없습니다.";
    }

    if (overlay) overlay.style.display = "flex";
}

function closeLifeDetails() {
    const overlay = document.getElementById("life-details-overlay");
    if (overlay) overlay.style.display = "none";
}

function initSocket(roomId) {
    if (globalSockets[roomId]) return;

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${location.host}/ws/chat/${roomId}?token=${accessToken}`);

    ws.onopen = () => setEngineStatus(true);
    ws.onclose = () => {
        delete globalSockets[roomId];
        if (roomId === currentRoomId) setEngineStatus(false);
    };
    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        handleIncomingData(roomId, data);
    };
    globalSockets[roomId] = ws;
}

// [v1.4.2 수정] 증분 렌더링 로직 적용 (메시지 유실 방지)
function handleIncomingData(roomId, data) {
    const f = friendsData.find((item) => item.room_id === roomId);
    if (!f) return;

    // 1. 히스토리 증분 업데이트 (기존과 비교하여 새 메시지만 append)
    if (data.history) {
        const oldLen = f.history ? f.history.length : 0;
        const newLen = data.history.length;
        f.history = data.history;

        if (roomId === currentRoomId) {
            const chatArea = document.getElementById("chat-area");
            if (newLen > oldLen) {
                // 새로운 메시지만 추가
                const newMsgs = data.history.slice(oldLen);
                newMsgs.forEach(m => appendMsg(m.role === 'user' ? 'user' : (m.role === 'system' ? 'system' : 'ai'), m.content, m.ts));
            } else if (newLen < oldLen || oldLen === 0) {
                // 초기화되었거나 큰 변화가 있을 때만 전체 다시 그림
                chatArea.innerHTML = "";
                data.history.forEach(m => appendMsg(m.role === 'user' ? 'user' : (m.role === 'system' ? 'system' : 'ai'), m.content, m.ts));
            }
        }
    }

    // 2. 상태 동기화
    const msgStatus = data.status || (data.current_status && data.current_status.status);
    if (msgStatus) {
        f.status = msgStatus;
        if (msgStatus === "online" && roomId === currentRoomId) {
            const overlay = document.getElementById("sync-overlay");
            if (overlay) overlay.style.display = "none";
        }
        if (currentView === "list") renderFriendList();
        if (roomId === currentRoomId) {
            const statusEl = document.getElementById("active-friend-status");
            if (statusEl) {
                statusEl.innerText = msgStatus === "online" ? "온라인" : "오프라인";
                statusEl.className = `status ${msgStatus}`;
            }
        }
    }

    // 3. 타이핑 상태
    if (roomId === currentRoomId && typeof data.typing !== "undefined") {
        const statusEl = document.getElementById("active-friend-status");
        if (statusEl) {
            if (data.typing) {
                statusEl.innerText = "입력 중...";
                statusEl.classList.add("typing");
            } else {
                statusEl.innerText = f.status === "online" ? "온라인" : "오프라인";
                statusEl.classList.remove("typing");
            }
        }
    }

    // 4. 발화 응답 처리 (WebSocket 직접 응답)
    if (data.responses) {
        data.responses.forEach((r) => {
            const msgObj = { role: "assistant", content: r.text, ts: r.ts };
            if (!f.history) f.history = [];

            // 이미 히스토리 업데이트를 통해 처리되었다면 중복 추가 방지
            const isDuplicate = f.history.some(h => h.content === r.text && h.ts === r.ts);
            if (!isDuplicate) {
                f.history.push(msgObj);
                if (roomId === currentRoomId && currentView === "chat") {
                    appendMsg("ai", r.text, r.ts);
                } else {
                    unreadCounts[roomId] = (unreadCounts[roomId] || 0) + 1;
                    updateBottomNavBadges();
                    showInAppNoti(f, r.text, roomId);
                    if (currentView === "chats") renderChatList();
                }
            }
        });
    }

    // 5. 관리자 제어 패널 데이터 실시간 동기화
    if (roomId === adminSelectedRoomId && data.current_status) {
        const status = data.current_status;
        const mPlan = document.getElementById("m-plan");
        const sPlan = document.getElementById("s-plan");
        if (mPlan && status.medium_term_plan) mPlan.innerText = status.medium_term_plan;
        if (sPlan && status.short_term_plan) sPlan.innerText = status.short_term_plan;

        const sfLog = document.getElementById("short-feeling-log");
        const mrLog = document.getElementById("medium-record-log");
        const fwLog = document.getElementById("fact-warehouse-log");

        if (sfLog && status.short_term_logs) {
            sfLog.innerText = status.short_term_logs.join("\n");
            sfLog.scrollTop = sfLog.scrollHeight;
        }
        if (mrLog && status.medium_term_logs) {
            mrLog.innerText = status.medium_term_logs.join("\n");
            mrLog.scrollTop = mrLog.scrollHeight;
        }
        if (fwLog && status.fact_warehouse) {
            fwLog.innerText = status.fact_warehouse.map((fact) => `• ${fact}`).join("\n");
            fwLog.scrollTop = fwLog.scrollHeight;
        }
        Object.assign(f, status);
        syncAdminUI(f);
    }
}

function showInAppNoti(friend, text, roomId) {
    const popup = document.getElementById("noti-popup");
    if (!popup) return;
    const colors = ["#FF9500", "#FF2D55", "#AF52DE", "#5AC8FA", "#34C759"];
    const avatar = document.getElementById("noti-avatar");

    if (friend.profile_image_url) {
        avatar.style.background = "none";
        avatar.innerHTML = `<img src="${friend.profile_image_url}" class="avatar-img" style="width:100%; height:100%; border-radius:inherit;">`;
    } else {
        avatar.style.background = colors[friend.name.charCodeAt(0) % 5];
        avatar.innerText = friend.name[0];
    }

    document.getElementById("noti-name").innerText = friend.name;
    document.getElementById("noti-text").innerText = text;
    notiTargetRoomId = roomId;

    popup.classList.add("show");
    setTimeout(() => popup.classList.remove("show"), 4000);
}

function handleNotiClick() {
    if (notiTargetRoomId) {
        joinRoom(notiTargetRoomId);
        const popup = document.getElementById("noti-popup");
        if (popup) popup.classList.remove("show");
    }
}

async function joinRoom(roomId) {
    currentRoomId = roomId;
    unreadCounts[roomId] = 0;
    updateBottomNavBadges();
    closeProfile(); // In case full profile is open
    switchMobileView("chat");

    let f = friendsData.find((item) => item.room_id === roomId);
    if (!f) {
        // If not found, it might be newly added. Wait for friends list to load.
        await loadFriends();
        f = friendsData.find((item) => item.room_id === roomId);
        if (!f) return; // Still not found
    }

    document.getElementById("active-friend-name").innerText = `${f.name} (${f.mbti})`;

    const overlay = document.getElementById("sync-overlay");
    if (f.status === "offline") {
        if (overlay) overlay.style.display = "flex";
    } else {
        if (overlay) overlay.style.display = "none";
    }

    const statusEl = document.getElementById("active-friend-status");
    if (statusEl) {
        statusEl.innerText = f.status === "online" ? "온라인" : "오프라인";
        statusEl.className = `status ${f.status || "offline"}`;
    }

    const chatArea = document.getElementById("chat-area");
    if (chatArea) {
        chatArea.innerHTML = "";
        if (f.history) {
            f.history.forEach((h) =>
                appendMsg(h.role === "user" ? "user" : (h.role === "system" ? "system" : "ai"), h.content, h.ts),
            );
        }
    }
    initSocket(roomId);
}

function syncAdminUI(f) {
    const keys = ["p_seriousness", "p_friendliness", "p_rationality", "p_slang", "v_likeability", "v_erotic", "v_v_mood", "v_relationship"];
    keys.forEach((k) => {
        const el = document.getElementById(k);
        if (el && document.activeElement !== el) el.value = f[k] ?? 50;
    });
}

function send() {
    const input = document.getElementById("msg-input");
    if (!input) return;
    const text = input.value;
    if (text && currentRoomId && globalSockets[currentRoomId]) {
        const ts = new Date().toTimeString().split(" ")[0];
        appendMsg("user", text, ts);
        const f = friendsData.find((item) => item.room_id === currentRoomId);
        if (f) {
            if (!f.history) f.history = [];
            f.history.push({ role: "user", content: text, ts: ts });
        }
        globalSockets[currentRoomId].send(text);
        input.value = "";
    }
}

function appendMsg(type, text, ts) {
    const area = document.getElementById("chat-area");
    if (!area) return;
    const row = document.createElement("div");
    row.className = `msg-row ${type}`;
    row.innerHTML = `<div class="bubble ${type}">${text}</div><div class="msg-meta">${ts}</div>`;
    area.appendChild(row);
    area.scrollTop = area.scrollHeight;
}

async function commitParams() {
    if (!adminSelectedRoomId) return;
    const keys = ["p_seriousness", "p_friendliness", "p_rationality", "p_slang", "v_likeability", "v_erotic", "v_v_mood", "v_relationship"];
    const params = {};
    keys.forEach((k) => {
        const el = document.getElementById(k);
        if (el) params[k] = parseInt(el.value) || 0;
    });
    const res = await fetch(`/update-params/${adminSelectedRoomId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify(params),
    });
    if (res.ok) alert("파라미터가 성공적으로 적용되었습니다.");
}

async function deleteFriend(event, roomId) {
    if (event && event.stopPropagation) event.stopPropagation();
    // [Fix] window.confirm() was blocked; removed — delete is immediate
    const res = await fetch(`/delete-friend/${roomId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (res.ok) {
        friendsData = friendsData.filter((item) => item.room_id !== roomId);
        selectedFriendRooms.delete(roomId);
        renderFriendList();
        renderChatList();
    }
}

// [Phase 5] 친구 목록 편집 모드 토글
function toggleFriendEditMode() {
    isEditMode = !isEditMode;
    selectedFriendRooms.clear();
    renderFriendList();
}

// [Phase 5] 편집 모드에서 개별 선택 토글
function toggleFriendSelect(roomId) {
    if (selectedFriendRooms.has(roomId)) {
        selectedFriendRooms.delete(roomId);
    } else {
        selectedFriendRooms.add(roomId);
    }
    // Update count label and re-render selected state
    const selCount = document.getElementById("friend-selected-count");
    if (selCount) selCount.innerText = `${selectedFriendRooms.size}개 선택됨`;

    // Re-render to reflect selection state visually
    renderFriendList();
}

// [Phase 5] 선택된 친구들 일괄 삭제
async function bulkDeleteFriends() {
    if (selectedFriendRooms.size === 0) return;

    const ids = [...selectedFriendRooms];
    // 삭제 중 UI 피드백
    const deleteBtn = document.querySelector("#friend-bulk-bar button:nth-of-type(1)");
    if (deleteBtn) deleteBtn.innerText = "삭제 중...";

    try {
        await Promise.all(ids.map(roomId =>
            fetch(`/delete-friend/${roomId}`, {
                method: "DELETE",
                headers: { Authorization: `Bearer ${accessToken}` },
            })
        ));

        selectedFriendRooms.clear();
        isEditMode = false;

        // 로컬 필터링 대신 서버 데이터를 새로 불러와서 UI 강제 갱신
        await loadFriends();
    } catch (e) {
        console.error("Bulk delete failed", e);
    } finally {
        if (deleteBtn) deleteBtn.innerText = "삭제";
    }
}

// [Phase 6] 친구 목록 전체 선택
function selectAllFriends() {
    if (selectedFriendRooms.size === friendsData.length) {
        selectedFriendRooms.clear();
    } else {
        friendsData.forEach(f => selectedFriendRooms.add(f.room_id));
    }
    const selCount = document.getElementById("friend-selected-count");
    if (selCount) selCount.innerText = `${selectedFriendRooms.size}개 선택됨`;
    renderFriendList();
}

// [Phase 6] 관리자 이브 목록 편집 모드 토글
function toggleAdminEveEditMode() {
    isAdminEveEditMode = !isAdminEveEditMode;
    selectedAdminEves.clear();

    const editBtn = document.getElementById("admin-eve-edit-btn");
    const bulkBar = document.getElementById("admin-eve-bulk-bar");
    const label = document.getElementById("admin-eve-selected-count");

    if (editBtn) editBtn.innerText = isAdminEveEditMode ? "완료" : "편집";
    if (bulkBar) bulkBar.style.display = isAdminEveEditMode ? "flex" : "none";
    if (label) label.innerText = "0개 선택됨";

    renderAdminEves();
}

// [Phase 6] 관리자 이브 편집 모드 개별 선택
function toggleAdminEveSelect(personaId) {
    if (selectedAdminEves.has(personaId)) {
        selectedAdminEves.delete(personaId);
    } else {
        selectedAdminEves.add(personaId);
    }
    const label = document.getElementById("admin-eve-selected-count");
    if (label) label.innerText = `${selectedAdminEves.size}개 선택됨`;
    renderAdminEves();
}

// [Phase 6] 관리자 이브 전체 선택
function selectAllAdminEves() {
    if (selectedAdminEves.size === adminEvesData.length) {
        selectedAdminEves.clear();
    } else {
        adminEvesData.forEach(eve => selectedAdminEves.add(eve.persona_id));
    }
    const label = document.getElementById("admin-eve-selected-count");
    if (label) label.innerText = `${selectedAdminEves.size}개 선택됨`;
    renderAdminEves();
}

// [Phase 6] 관리자 이브 일괄 삭제
async function bulkDeleteAdminEves() {
    if (selectedAdminEves.size === 0) return;
    if (!confirm(`선택한 ${selectedAdminEves.size}명의 이브를 영구 삭제하시겠습니까?\n(피드, 관계망, 채팅방 정보가 모두 삭제됩니다.)`)) return;

    const ids = [...selectedAdminEves];
    const deleteBtn = document.querySelector("#admin-eve-bulk-bar button:nth-of-type(2)");
    if (deleteBtn) deleteBtn.innerText = "삭제 중...";

    try {
        const res = await fetch("/admin/bulk-delete-personas", {
            method: "DELETE",
            headers: {
                "Authorization": `Bearer ${accessToken}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ ids })
        });

        if (res.ok) {
            selectedAdminEves.clear();
            isAdminEveEditMode = false;
            const bulkBar = document.getElementById("admin-eve-bulk-bar");
            if (bulkBar) bulkBar.style.display = "none";
            const editBtn = document.getElementById("admin-eve-edit-btn");
            if (editBtn) editBtn.innerText = "편집";

            alert(`${ids.length}명의 이브가 삭제되었습니다.`);
            loadAdminEves();
        } else {
            throw new Error("Failed to delete personas");
        }
    } catch (err) {
        console.error("Bulk delete admin error:", err);
        alert("삭제 중 오류가 발생했습니다.");
    } finally {
        if (deleteBtn) deleteBtn.innerText = "삭제";
    }
}

function setEngineStatus(isActive) {
    const dot = document.getElementById("engine-dot");
    const text = document.getElementById("engine-text");
    if (dot) dot.className = isActive ? "status-dot active" : "status-dot";
    if (text) text.innerText = isActive ? "Active" : "Offline";

    // 관리자 페이지용 도트도 함께 동기화
    const dotAdmin = document.getElementById("engine-dot-admin");
    const textAdmin = document.getElementById("engine-text-admin");
    if (dotAdmin) dotAdmin.className = isActive ? "status-dot active" : "status-dot";
    if (textAdmin) textAdmin.innerText = isActive ? "System Running" : "System Offline";
}

async function addFriend() {
    const btn = document.getElementById("add-friend-btn");
    const list = document.getElementById("friend-list");
    if (!btn || !list) return;

    btn.innerText = "+ 친구 찾는 중...";
    btn.disabled = true;

    const scanningCard = document.createElement("div");
    scanningCard.className = "friend-item portal-card is-scanning";
    scanningCard.innerHTML = `
        <div class="friend-avatar scanning-circle"></div>
        <div class="friend-info"><div class="name-row"><span class="name">Portal Scanning...</span></div><div class="last-msg">인연을 찾는 중입니다.</div></div>
    `;
    list.prepend(scanningCard);
    list.scrollTop = 0;

    try {
        const res = await fetch("/add-friend", { method: "POST", headers: { Authorization: `Bearer ${accessToken}` } });
        if (res.ok) {
            scanningCard.classList.remove("is-scanning");
            scanningCard.classList.add("is-success");
            scanningCard.querySelector(".name").innerText = "Portal Connected!";
            setTimeout(async () => {
                btn.innerText = "+ 새로운 친구 찾기"; btn.disabled = false;
                await loadFriends();
            }, 1500);
        } else { throw new Error("Fail"); }
    } catch (e) {
        alert("실패했습니다."); scanningCard.remove();
        btn.innerText = "+ 새로운 친구 찾기"; btn.disabled = false;
    }
}

async function resetDB() {
    if (confirm("DB를 초기화합니까? 모든 데이터가 영구 삭제됩니다.")) {
        await fetch("/reset-db", { method: "POST", headers: { Authorization: `Bearer ${accessToken}` } });
        location.reload();
    }
}

async function shutdownServer() {
    if (confirm("서버를 종료합니까?"))
        await fetch("/shutdown", { method: "POST", headers: { Authorization: `Bearer ${accessToken}` } });
}

async function loadAdminUsers() {
    const res = await fetch("/admin/users", { headers: { Authorization: `Bearer ${accessToken}` } });
    if (res.ok) {
        const users = await res.json();
        const list = document.getElementById("admin-user-list");
        if (!list) return;
        list.innerHTML = users.map((u) => `
            <div class="user-info-card" onclick="viewUserDetail(${u.id}, '${u.username}')">
                <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                    <span style="font-weight:900;">${u.username} ${u.is_admin ? "(Admin)" : ""}</span>
                    <span class="token-badge" style="font-size:10px; font-weight:800; color:var(--blue);">$ ${u.total_spent_usd.toFixed(4)}</span>
                </div>
                <div style="font-size:10px; color:var(--text-sub);">가입: ${new Date(u.created_at).toLocaleDateString()} / 활동: ${new Date(u.last_active).toLocaleString()}</div>
            </div>
        `).join("");
    }
}

async function viewUserDetail(userId, username) {
    const res = await fetch(`/admin/user/${userId}/detail`, { headers: { Authorization: `Bearer ${accessToken}` } });
    if (res.ok) {
        const rooms = await res.json();
        const userView = document.getElementById("admin-user-view");
        const detailView = document.getElementById("admin-detail-view");
        if (userView) userView.style.display = "none";
        if (detailView) detailView.style.display = "block";

        document.getElementById("admin-detail-title").innerText = `${username}'s EVEs`;
        const content = document.getElementById("admin-detail-content");
        if (content) {
            content.innerHTML = rooms.map((r) => `
                        <div class="user-info-card" style="cursor:default; margin-bottom:20px; border:1px solid var(--border);">
                            <div style="font-weight:900; font-size:14px; margin-bottom:10px; color:var(--blue);">${r.persona_name} (ID: ${r.room_id})</div>
                            <div class="panel-title">FULL CHAT HISTORY</div>
                            <div class="dev-log-window" style="height:150px; background:#fff; color:var(--text-main); border:1px solid var(--border); font-size:10px;">${r.history?.map(h => `<div><b>${h.role}:</b> ${h.content}</div>`).join('') || '내역 없음'}</div>
                            <div class="panel-title">FACT WAREHOUSE</div>
                            <div class="dev-log-window" style="height:80px; min-height:80px; background:#fff; color:var(--text-main); border:1px solid var(--border); font-size:10px;">${r.fact_warehouse?.map(f => `• ${f}`).join('<br>') || '저장된 사실 없음'}</div>
                        </div>
                    `).join("");
        }
        document.getElementById("admin-delete-user-btn").onclick = () => adminDeleteUser(userId, username);
    }
}

async function adminDeleteUser(userId, username) {
    if (confirm(`'${username}' 계정을 삭제하시겠습니까? 관련 데이터가 모두 소멸됩니다.`)) {
        const res = await fetch(`/admin/user/${userId}`, { method: "DELETE", headers: { Authorization: `Bearer ${accessToken}` } });
        if (res.ok) { alert("계정이 삭제되었습니다."); switchAdminTab("users"); }
    }
}

async function loadAdminIdentity() {
    if (!adminSelectedRoomId) return;
    const res = await fetch(`/admin/room/${adminSelectedRoomId}/volatile`, { headers: { Authorization: `Bearer ${accessToken}` } });
    if (res.ok) {
        const data = await res.json();
        const p = data.p_dict || {};
        const schedule = document.getElementById("edit-schedule");
        const details = document.getElementById("edit-details");
        const model = document.getElementById("admin-model-select");
        const imgPrompt = document.getElementById("admin-profile-image-prompt");
        const imgPreview = document.getElementById("admin-profile-image-preview");
        const imgStatus = document.getElementById("admin-profile-image-status");
        const galleryPreview = document.getElementById("admin-profile-gallery-preview");
        const eveRow = adminEvesData.find((eve) => (eve.rooms || []).some((r) => Number(r.room_id) === Number(adminSelectedRoomId)));
        if (schedule) schedule.value = JSON.stringify(p.daily_schedule || [], null, 2);
        if (details) details.value = JSON.stringify(p.profile_details || {}, null, 2);
        if (model) model.value = data.model_id || "gemini-3-flash-preview";
        if (imgPrompt) imgPrompt.value = (eveRow?.image_prompt || p.image_prompt || "").trim();
        if (imgPreview) {
            const url = eveRow?.persona_image || p.profile_image_url || "";
            imgPreview.src = url;
            imgPreview.style.display = url ? "block" : "none";
        }
        if (galleryPreview) {
            const gallery = Array.isArray(eveRow?.profile_images) ? eveRow.profile_images : [];
            galleryPreview.innerHTML = gallery.map((item) => {
                const url = (item && typeof item === "object") ? item.url : item;
                return url ? `<img class="profile-gallery-thumb" src="${url}" alt="img">` : "";
            }).join("");
        }
        if (imgStatus) imgStatus.innerText = "Ready";
    }
}

async function saveIdentity() {
    if (!adminSelectedRoomId) return;
    try {
        const schedule = JSON.parse(document.getElementById("edit-schedule").value);
        const details = JSON.parse(document.getElementById("edit-details").value);
        const modelId = document.getElementById("admin-model-select").value;
        const res = await fetch(`/admin/room/${adminSelectedRoomId}/identity`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
            body: JSON.stringify({ daily_schedule: schedule, profile_details: details, model_id: modelId }),
        });
        if (res.ok) alert("정체성이 저장되었습니다.");
    } catch (e) { alert("JSON 형식이 올바르지 않습니다."); }
}

async function adminGenerateProfileImage() {
    if (!adminSelectedRoomId) return;
    const promptEl = document.getElementById("admin-profile-image-prompt");
    const useBaseEl = document.getElementById("admin-profile-use-base");
    const modelEl = document.getElementById("admin-profile-image-model");
    const statusEl = document.getElementById("admin-profile-image-status");
    const previewEl = document.getElementById("admin-profile-image-preview");
    const galleryPreview = document.getElementById("admin-profile-gallery-preview");
    const btn = document.getElementById("admin-profile-generate-btn");
    const prompt = (promptEl?.value || "").trim();
    if (!prompt) {
        if (statusEl) statusEl.innerText = "프롬프트를 입력하세요.";
        return;
    }
    try {
        if (btn) btn.disabled = true;
        if (statusEl) statusEl.innerText = "Generating...";
        const res = await fetch(`/admin/room/${adminSelectedRoomId}/profile-image`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
            body: JSON.stringify({
                prompt,
                prefer_edit: (modelEl?.value === "edit") ? true : !!useBaseEl?.checked
            })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data?.detail || "image generation failed");
        }
        if (previewEl && data.image_url) {
            previewEl.src = data.image_url;
            previewEl.style.display = "block";
        }
        if (statusEl) statusEl.innerText = `Done (${data.model || "model"})`;

        // Sync local admin list cache so list/profile reflects latest image immediately.
        const eve = adminEvesData.find((item) => Number(item.persona_id) === Number(data.persona_id));
        if (eve) {
            eve.persona_image = data.image_url;
            eve.image_prompt = prompt;
            if (Array.isArray(data.profile_images)) eve.profile_images = data.profile_images;
        }
        if (galleryPreview && Array.isArray(data.profile_images)) {
            galleryPreview.innerHTML = data.profile_images.map((item) => {
                const url = (item && typeof item === "object") ? item.url : item;
                return url ? `<img class="profile-gallery-thumb" src="${url}" alt="img">` : "";
            }).join("");
        }
        renderAdminEves();
    } catch (e) {
        console.error("adminGenerateProfileImage error", e);
        if (statusEl) statusEl.innerText = `Failed: ${e.message || ""}`;
        alert(`프로필 이미지 생성 실패: ${e.message || ""}`);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function adminAddProfileImage() {
    if (!adminSelectedRoomId) return;
    const promptEl = document.getElementById("admin-profile-image-prompt");
    const modelEl = document.getElementById("admin-profile-image-model");
    const statusEl = document.getElementById("admin-profile-image-status");
    const previewEl = document.getElementById("admin-profile-image-preview");
    const galleryPreview = document.getElementById("admin-profile-gallery-preview");
    const btn = document.getElementById("admin-profile-add-btn");
    const prompt = (promptEl?.value || "").trim();
    const model = (modelEl?.value || "flux").trim();
    if (!prompt) {
        if (statusEl) statusEl.innerText = "프롬프트를 입력하세요.";
        return;
    }
    try {
        if (btn) btn.disabled = true;
        if (statusEl) statusEl.innerText = "Adding photo...";
        const res = await fetch(`/admin/room/${adminSelectedRoomId}/profile-image/add`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
            body: JSON.stringify({ prompt, model })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail || "add photo failed");

        if (previewEl && data.image_url) {
            previewEl.src = data.image_url;
            previewEl.style.display = "block";
        }
        if (galleryPreview && Array.isArray(data.profile_images)) {
            galleryPreview.innerHTML = data.profile_images.map((item) => {
                const url = (item && typeof item === "object") ? item.url : item;
                return url ? `<img class="profile-gallery-thumb" src="${url}" alt="img">` : "";
            }).join("");
        }
        const eve = adminEvesData.find((item) => Number(item.persona_id) === Number(data.persona_id));
        if (eve && Array.isArray(data.profile_images)) {
            eve.profile_images = data.profile_images;
            if (!eve.persona_image && data.profile_images[0]) {
                const first = (data.profile_images[0] && typeof data.profile_images[0] === "object")
                    ? data.profile_images[0].url
                    : data.profile_images[0];
                eve.persona_image = first || eve.persona_image;
            }
        }
        if (statusEl) statusEl.innerText = `Added (${data.model || "model"})`;
        renderAdminEves();
    } catch (e) {
        console.error("adminAddProfileImage error", e);
        if (statusEl) statusEl.innerText = `Failed: ${e.message || ""}`;
        alert(`추가 이미지 생성 실패: ${e.message || ""}`);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function sendGhostMsg() {
    if (!adminSelectedRoomId) return;
    const input = document.getElementById("ghost-msg-input");
    if (!input) return;
    const text = input.value;
    if (!text) return;
    const res = await fetch(`/admin/room/${adminSelectedRoomId}/ghost-write`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify({ text: text, role: "assistant" }),
    });
    if (res.ok) input.value = "";
}

async function loadPromptTemplate() {
    const keySel = document.getElementById("prompt-key-select");
    if (!keySel) return;
    const key = keySel.value;
    const res = await fetch("/admin/prompts", { headers: { Authorization: `Bearer ${accessToken}` } });
    if (res.ok) {
        const prompts = await res.json();
        const pt = prompts.find(p => p.key === key);
        const editArea = document.getElementById("edit-prompt-template");
        if (editArea) editArea.value = pt ? pt.template : "";
    }
}

async function savePromptTemplate() {
    const key = document.getElementById("prompt-key-select").value;
    const template = document.getElementById("edit-prompt-template").value;
    const res = await fetch("/admin/prompts", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify({ key: key, template: template }),
    });
    if (res.ok) alert("전략 지침이 업데이트되었습니다.");
}

async function loadDebugLogs() {
    const res = await fetch("/admin/debug-logs", { headers: { Authorization: `Bearer ${accessToken}` } });
    if (res.ok) {
        const logs = await res.json();
        const list = document.getElementById("admin-debug-log-list");
        if (!list) return;
        list.innerHTML = logs.reverse().map(l => `
            <div class="debug-log-item ${l.engine_type.includes('ERROR') ? 'error' : ''}">
                <div style="display:flex; justify-content:space-between; margin-bottom:5px;"><b style="color:var(--blue);">${l.engine_type}</b><span style="opacity:0.6;">${l.ts}</span></div>
                <div style="font-size:9px; color:var(--text-sub); margin-bottom:5px;">Room: ${l.room_id} | Model: ${l.model}</div>
                <div style="background:#f8f8f9; padding:5px; border-radius:4px; max-height:60px; overflow-y:auto; margin-bottom:5px; white-space:pre-wrap; font-size:9px;">${l.prompt}</div>
                <div style="background:#eef; padding:5px; border-radius:4px; max-height:60px; overflow-y:auto; white-space:pre-wrap; border-left:2px solid var(--blue); font-size:9px;">${l.response}</div>
            </div>
        `).join("");
    }
}

async function sendGlobalNotice() {
    const titleArea = document.getElementById("global-notice-title");
    const contentArea = document.getElementById("global-notice-content");
    if (!contentArea || !titleArea) return;
    const title = titleArea.value;
    const content = contentArea.value;
    if (!content || !title) return alert("제목과 내용을 입력하세요.");
    if (confirm("모든 활성 대화방에 공지를 전송하시겠습니까?")) {
        const res = await fetch("/admin/global-notice", {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
            body: JSON.stringify({ title: title, content: content }),
        });
        if (res.ok) {
            alert("공지가 브로드캐스트되었습니다.");
            titleArea.value = "";
            contentArea.value = "";
        }
    }
}

function updateUIByAuth() {
    const userDisplay = document.getElementById("user-display");
    const navDev = document.getElementById("nav-dev");
    const logoutBtn = document.getElementById("logout-btn");
    if (accessToken) {
        if (userDisplay) userDisplay.innerText = currentUsername || "";

        // [v2.0.0] 로그인 성공 시 서버 상태 Active로 표시
        setEngineStatus(true);

        if (isAdmin) {
            document.body.classList.add("dev-unlocked");
            if (navDev) navDev.style.display = "flex";
        } else {
            if (navDev) navDev.style.display = "none";
        }
        if (logoutBtn) {
            logoutBtn.innerText = "로그아웃";
            logoutBtn.style.background = "#ff3b30";
            logoutBtn.style.color = "white";
        }
    } else {
        if (userDisplay) userDisplay.innerText = "";
        if (navDev) navDev.style.display = "none";
        document.body.classList.remove("dev-unlocked", "is-dev");
        if (logoutBtn) {
            logoutBtn.innerText = "로그인";
            logoutBtn.style.background = "#22c55e";
            logoutBtn.style.color = "white";
        }
    }
    updateBottomNavBadges();
}

// =================================================
// [v1.5.0] 온보딩 플로우 함수
// =================================================

// =================================================
// [v1.5.0] 프로필 작성 함수
// =================================================

// 프로필 이미지 미리보기
function previewProfileImage() {
    const fileInput = document.getElementById("profile-image-input");
    const preview = document.getElementById("preview-avatar");
    const galleryPreview = document.getElementById("profile-gallery-preview");

    if (fileInput.files && fileInput.files[0]) {
        const reader = new FileReader();
        reader.onload = function (e) {
            preview.style.background = "none";
            preview.innerHTML = `<img src="${e.target.result}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">`;
        };
        reader.readAsDataURL(fileInput.files[0]);

        if (galleryPreview) {
            const files = Array.from(fileInput.files).slice(0, 3);
            galleryPreview.innerHTML = "";
            files.forEach((file) => {
                const fr = new FileReader();
                fr.onload = (evt) => {
                    const url = evt.target?.result || "";
                    if (!url) return;
                    galleryPreview.insertAdjacentHTML("beforeend", `<img class="profile-gallery-thumb" src="${url}" alt="preview">`);
                };
                fr.readAsDataURL(file);
            });
        }
    }
}

async function resizeImageFileToDataUrl(file, options = {}) {
    const maxSide = Number(options.maxSide || 1024);
    const quality = Number(options.quality || 0.86);

    const readAsDataUrl = (blob) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve(e.target.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
    });

    const originalDataUrl = await readAsDataUrl(file);
    const image = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = originalDataUrl;
    });

    const srcW = image.width || 0;
    const srcH = image.height || 0;
    if (!srcW || !srcH) return originalDataUrl;

    const longest = Math.max(srcW, srcH);
    if (longest <= maxSide) return originalDataUrl;

    const scale = maxSide / longest;
    const dstW = Math.max(1, Math.round(srcW * scale));
    const dstH = Math.max(1, Math.round(srcH * scale));
    const canvas = document.createElement("canvas");
    canvas.width = dstW;
    canvas.height = dstH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return originalDataUrl;

    ctx.drawImage(image, 0, 0, dstW, dstH);
    return canvas.toDataURL("image/jpeg", quality);
}

// 기본 아바타 생성 (아이디 첫 글자 + 랜덤 색상)
function generateDefaultAvatar(username) {
    const preview = document.getElementById("preview-avatar");
    const firstLetter = username ? username[0].toUpperCase() : "?";
    const colors = [
        "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
        "linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%)",
        "linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)",
        "linear-gradient(135deg, #fbc2eb 0%, #a6c1ee 100%)",
        "linear-gradient(135deg, #84fab0 0%, #8fd3f4 100%)"
    ];
    const color = colors[username ? username.charCodeAt(0) % colors.length : 0];

    if (preview) {
        preview.style.background = color;
        preview.innerHTML = firstLetter;
    }
}

// 프로필 작성 화면 표시
function showProfileSetup() {
    document.getElementById("auth-overlay").style.display = "none";
    const profileOverlay = document.getElementById("profile-setup-overlay");
    if (profileOverlay) {
        profileOverlay.style.display = "flex";
        generateDefaultAvatar(currentUsername);
        setProfileMbtiDial("INFP");
    }
}

function setProfileMbtiDial(mbti = "INFP") {
    const normalized = (mbti || "INFP").toUpperCase();
    const axes = [
        { id: "mbti-axis-ei", value: normalized[0] === "E" ? "E" : "I" },
        { id: "mbti-axis-sn", value: normalized[1] === "S" ? "S" : "N" },
        { id: "mbti-axis-tf", value: normalized[2] === "T" ? "T" : "F" },
        { id: "mbti-axis-jp", value: normalized[3] === "J" ? "J" : "P" }
    ];
    axes.forEach(({ id, value }) => {
        const axis = document.getElementById(id);
        if (!axis) return;
        const items = [...axis.querySelectorAll(".mbti-choice-btn")];
        items.forEach((el) => {
            el.classList.toggle("active", el.dataset.value === value);
            el.onclick = () => {
                axis.querySelectorAll(".mbti-choice-btn").forEach((s) => s.classList.remove("active"));
                el.classList.add("active");
                updateProfileMbtiPreview();
            };
        });
    });
    updateProfileMbtiPreview();
}

function getProfileMbtiFromDial() {
    const pick = (axisId, fallback) => {
        const active = document.querySelector(`#${axisId} .mbti-choice-btn.active`);
        return active?.dataset?.value || fallback;
    };
    return `${pick("mbti-axis-ei", "I")}${pick("mbti-axis-sn", "N")}${pick("mbti-axis-tf", "F")}${pick("mbti-axis-jp", "P")}`;
}

function updateProfileMbtiPreview() {
    const el = document.getElementById("profile-mbti-preview");
    if (el) el.innerText = getProfileMbtiFromDial();
}

// 프로필 저장
async function saveUserProfile() {
    const btn = document.querySelector("#profile-setup-overlay .auth-btn-main");

    // 로딩 표시
    const originalBtnText = btn.innerText;
    btn.innerText = "저장 중...";
    btn.disabled = true;
    try {
        const displayName = document.getElementById("profile-display-name").value;
        if (!displayName) {
            throw new Error("이름은 필수 입력 항목입니다.");
        }

        const profileData = {
            display_name: displayName,
            age: parseInt(document.getElementById("profile-age").value) || null,
            gender: document.getElementById("profile-gender").value || null,
            mbti: getProfileMbtiFromDial(),
            profile_details: {
                hook: document.getElementById("profile-hook").value || ""
            }
        };

        console.log("Saving profile data:", profileData);

        if (!accessToken) {
            throw new Error("인증 토큰이 없습니다. 다시 로그인해주세요.");
        }

        const res = await fetch("/api/user/profile", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${accessToken}`
            },
            body: JSON.stringify(profileData)
        });

        if (res.ok) {
            // 프로필 이미지가 있으면 업로드
            const fileInput = document.getElementById("profile-image-input");
            if (fileInput.files && fileInput.files[0]) {
                btn.innerText = "이미지 업로드 중...";
                try {
                    const files = Array.from(fileInput.files).slice(0, 3);
                    const imageUrls = [];
                    for (const file of files) {
                        const resizedDataUrl = await resizeImageFileToDataUrl(file, {
                            maxSide: 1024,
                            quality: 0.86
                        });
                        imageUrls.push(resizedDataUrl);
                    }

                    const imgRes = await fetch("/api/user/profile/images", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "Authorization": `Bearer ${accessToken}`
                        },
                        body: JSON.stringify({ image_urls: imageUrls })
                    });
                    if (!imgRes.ok) console.error("Image upload failed");
                } catch (imgErr) {
                    console.error("Image processing error:", imgErr);
                }
            }

            // 프로필 작성 완료 - 메인 화면으로 이동
            alert("프로필이 저장되었습니다.");
            document.getElementById("profile-setup-overlay").style.display = "none";

            // 이름과 정보가 업데이트되었으므로 UI 갱신
            currentUsername = displayName; // 임시 반영
            updateUIByAuth();

            loadFriends();
        } else {
            const errData = await res.json();
            throw new Error(errData.detail || "알 수 없는 오류");
        }
    } catch (e) {
        console.error("Profile save error:", e);
        alert("프로필 저장 실패: " + e.message);
        btn.innerText = originalBtnText;
        btn.disabled = false;
    }
}

// =================================================
// [v1.5.0] 설정 관리 함수
// =================================================

// 테마 적용 함수
function applyTheme(theme) {
    if (theme === 'light') {
        document.body.classList.add('light-mode');
    } else {
        document.body.classList.remove('light-mode');
    }
}

// 설정 로드
async function loadSettings() {
    try {
        const res = await fetch("/api/user/settings", {
            headers: { "Authorization": `Bearer ${accessToken}` }
        });

        if (res.ok) {
            const settings = await res.json();

            const genderFilter = document.getElementById("eve-gender-filter");
            const notifications = document.getElementById("notifications-enabled");
            const theme = document.getElementById("theme-select");

            if (genderFilter) genderFilter.value = settings.eve_gender_filter || "all";
            if (notifications) notifications.checked = settings.notifications_enabled !== false;

            const currentTheme = settings.theme || "dark";
            if (theme) theme.value = currentTheme;
            applyTheme(currentTheme);
        }
    } catch (e) {
        console.log("설정 로드 실패:", e);
    }
}

// 설정 저장
async function saveSettings() {
    const themeVal = document.getElementById("theme-select")?.value || "dark";

    // 즉시 테마 적용
    applyTheme(themeVal);

    const settings = {
        eve_gender_filter: document.getElementById("eve-gender-filter")?.value || "all",
        notifications_enabled: document.getElementById("notifications-enabled")?.checked ?? true,
        theme: themeVal
    };

    try {
        await fetch("/api/user/settings", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${accessToken}`
            },
            body: JSON.stringify(settings)
        });
    } catch (e) {
        console.log("설정 저장 실패:", e);
    }
}

// 내 프로필 수정 (프로필 작성 화면 재사용)
async function editMyProfile() {
    try {
        const res = await fetch("/api/user/profile", {
            headers: { "Authorization": `Bearer ${accessToken}` }
        });

        if (res.ok) {
            const profile = await res.json();

            // 기존 값으로 폼 채우기
            if (profile.display_name) document.getElementById("profile-display-name").value = profile.display_name;
            if (profile.age) document.getElementById("profile-age").value = profile.age;
            if (profile.gender) document.getElementById("profile-gender").value = profile.gender;
            setProfileMbtiDial(profile.mbti || "INFP");

            if (profile.profile_details) {
                document.getElementById("profile-hook").value = profile.profile_details.hook || "";
            }

            // 아바타 미리보기
            if (profile.profile_image_url) {
                const preview = document.getElementById("preview-avatar");
                preview.style.background = "none";
                preview.innerHTML = `<img src="${profile.profile_image_url}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">`;
            } else {
                generateDefaultAvatar(profile.display_name || currentUsername);
            }
            const galleryPreview = document.getElementById("profile-gallery-preview");
            if (galleryPreview) {
                const images = Array.isArray(profile.profile_images) ? profile.profile_images : [];
                galleryPreview.innerHTML = images
                    .map((item) => {
                        const url = (item && typeof item === "object") ? item.url : item;
                        return url ? `<img class="profile-gallery-thumb" src="${url}" alt="photo">` : "";
                    })
                    .join("");
            }

            // 프로필 작성 화면 표시
            document.getElementById("profile-setup-overlay").style.display = "flex";
        }
    } catch (e) {
        alert("프로필 로드에 실패했습니다.");
    }
}

// [v2.0.0] Social Feed Logic
function bindFeedInfiniteScroll() {
    if (feedScrollBound) return;
    const container = document.getElementById("feed-list");
    if (!container) return;
    container.addEventListener("scroll", () => {
        if (feedLoading || !feedHasMore) return;
        const threshold = 240;
        if (container.scrollTop + container.clientHeight >= container.scrollHeight - threshold) {
            loadFeed(false);
        }
    });
    feedScrollBound = true;
}

function bindFeedPullToRefresh() {
    if (feedPullBound) return;
    const container = document.getElementById("feed-list");
    const indicator = document.getElementById("feed-pull-indicator");
    if (!container) return;

    let startY = 0;
    let pulling = false;
    let pull = 0;
    const threshold = 72;

    container.addEventListener("touchstart", (e) => {
        if (container.scrollTop > 0) return;
        startY = e.touches[0].clientY;
        pulling = true;
        pull = 0;
    }, { passive: true });

    container.addEventListener("touchmove", (e) => {
        if (!pulling) return;
        const y = e.touches[0].clientY;
        const delta = y - startY;
        if (delta <= 0 || container.scrollTop > 0) return;
        pull = Math.min(100, delta * 0.45);
        container.style.transform = `translateY(${pull}px)`;
        if (indicator) {
            indicator.style.display = "block";
            indicator.innerText = pull >= threshold ? "놓으면 새로고침" : "당겨서 새로고침";
        }
    }, { passive: true });

    container.addEventListener("touchend", async () => {
        if (!pulling) return;
        pulling = false;
        container.style.transform = "translateY(0)";
        if (indicator) indicator.style.display = "none";
        if (pull >= threshold) {
            await loadFeed(true);
        }
        pull = 0;
    });

    feedPullBound = true;
}

async function loadFeed(reset = false) {
    const container = document.getElementById("feed-list");
    const composer = document.getElementById("user-feed-composer");
    if (composer) composer.style.display = accessToken ? "block" : "none";
    if (!container) return;
    if (feedLoading) return;
    if (!reset && !feedHasMore) return;

    if (reset) {
        feedOffset = 0;
        feedHasMore = true;
        // [Phase 5] Skeleton Loading UI
        container.innerHTML = Array(3).fill(`
            <article class="feed-card skeleton-card">
                <div class="feed-avatar-col"><div class="skeleton-avatar"></div></div>
                <div class="feed-content-col">
                    <div class="skeleton-text short"></div>
                    <div class="skeleton-text long"></div>
                    <div class="skeleton-box"></div>
                </div>
            </article>
        `).join("");
    }

    feedLoading = true;
    try {
        const headers = {};
        if (accessToken) headers.Authorization = `Bearer ${accessToken}`;

        const res = await fetch(`/api/feed?offset=${feedOffset}&limit=${FEED_PAGE_SIZE}`, { headers });
        if (res.ok) {
            const data = await res.json();
            const posts = Array.isArray(data) ? data : (data.items || []);
            const hasMore = Array.isArray(data) ? (posts.length === FEED_PAGE_SIZE) : !!data.has_more;
            _ingestFeedPostsForBadge(posts, currentView === "feed");
            renderFeed(posts, !reset);
            feedOffset += posts.length;
            feedHasMore = hasMore;
            bindFeedInfiniteScroll();
            bindFeedPullToRefresh();
        }
    } catch (e) {
        console.error("Feed load failed", e);
        if (container) container.innerHTML = "<div style='padding:20px; text-align:center;'>Feed failed to load.</div>";
    } finally {
        feedLoading = false;
    }
}

function openEveProfileFromFeed(personaId, roomId) {
    if (!personaId) return;
    let targetRoomId = roomId || null;
    if (!targetRoomId) {
        const friend = friendsData.find((f) => f.persona_id === personaId);
        if (friend) targetRoomId = friend.room_id;
    }
    if (targetRoomId) {
        openProfile(targetRoomId);
        return;
    }
    fetch(`/api/public/persona/${personaId}`, {
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
    })
        .then((r) => (r.ok ? r.json() : null))
        .then((p) => {
            if (!p) {
                openMiniProfile(personaId, null);
                return;
            }
            renderFullProfile({
                room_id: null,
                persona_id: p.id,
                name: p.name,
                age: p.age,
                gender: p.gender,
                mbti: p.mbti,
                profile_image_url: p.profile_image_url,
                profile_images: Array.isArray(p.profile_images) ? p.profile_images : [],
                image_prompt: "",
                profile_details: p.profile_details || { hook: "" },
                diaries: Array.isArray(p.diaries) ? p.diaries : [],
                relationship_category: "Not friends",
                p_seriousness: null,
                p_friendliness: null,
                p_rationality: null,
                p_slang: null,
                v_likeability: null,
                v_v_mood: null,
                v_relationship: null,
                v_erotic: null
            });

            const chatBtn = document.getElementById("fp-chat-btn");
            const delBtn = document.getElementById("fp-delete-btn");
            const relBtn = document.getElementById("fp-rel-btn");
            const lifeBtn = document.getElementById("fp-life-btn");
            if (chatBtn) {
                chatBtn.innerText = "Add Friend";
                chatBtn.onclick = () => {
                    openMiniProfile(personaId, null);
                    closeProfile();
                };
            }
            if (delBtn) delBtn.style.display = "none";
            if (relBtn) relBtn.style.display = "none";
            if (lifeBtn) lifeBtn.style.display = "none";

            document.body.classList.add("is-profile");
            const profilePage = document.getElementById("profile-page");
            if (profilePage) profilePage.scrollTop = 0;
        })
        .catch(() => openMiniProfile(personaId, null));
}

function onUserFeedImageSelected(inputEl) {
    const file = inputEl?.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
        userFeedImageData = e.target.result || "";
        const preview = document.getElementById("user-feed-image-preview");
        const pill = document.getElementById("user-feed-image-pill");
        if (preview && userFeedImageData) {
            preview.src = userFeedImageData;
        }
        if (pill) pill.style.display = "inline-flex";
    };
    reader.readAsDataURL(file);
}

function triggerFeedImagePicker() {
    const input = document.getElementById("user-feed-image-file");
    if (input) input.click();
}

function clearUserFeedImage() {
    userFeedImageData = "";
    const input = document.getElementById("user-feed-image-file");
    const preview = document.getElementById("user-feed-image-preview");
    const pill = document.getElementById("user-feed-image-pill");
    if (input) input.value = "";
    if (preview) {
        preview.src = "";
    }
    if (pill) pill.style.display = "none";
}

async function submitUserFeedPost() {
    if (!accessToken) return showAuthModal();
    const contentEl = document.getElementById("user-feed-content");
    const submitBtn = document.getElementById("user-feed-submit-btn");
    if (!contentEl || !submitBtn) return;

    const content = contentEl.value.trim();
    if (!content) {
        alert("피드 내용을 입력해주세요.");
        return;
    }

    const payload = { content };
    if (userFeedImageData) payload.image_url = userFeedImageData;

    try {
        submitBtn.disabled = true;
        submitBtn.style.opacity = 0.6;
        const res = await fetch("/api/feed/post", {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${accessToken}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            let msg = "피드 업로드 실패";
            try {
                const err = await res.json();
                msg = err.detail || msg;
            } catch (_) { }
            throw new Error(msg);
        }

        contentEl.value = "";
        clearUserFeedImage();
        await loadFeed(true);
    } catch (e) {
        alert(e.message || "피드 업로드 실패");
    } finally {
        submitBtn.disabled = false;
        submitBtn.style.opacity = 1;
    }
}

function renderFeed(posts, append = false) {
    const container = document.getElementById("feed-list");
    if (!container) return;

    const html = posts.map(post => {
        const promptParam = (isAdmin && post.image_prompt) ? encodeURIComponent(post.image_prompt) : "";
        const canOpenPersonaProfile = post.author_type === "persona" && post.author_id;
        const profileClick = canOpenPersonaProfile ? `onclick="openEveProfileFromFeed(${post.author_id}, ${post.room_id ? post.room_id : 'null'})"` : "";
        const dmClick = canOpenPersonaProfile ? `onclick="handleFeedDMClick(${post.author_id}, ${post.room_id ? post.room_id : 'null'})"` : "";
        const imageClick = canOpenPersonaProfile
            ? (isAdmin
                ? `onclick="openLightbox('${post.image_url}', '${promptParam}')"`
                : `onclick="openEveProfileFromFeed(${post.author_id}, ${post.room_id ? post.room_id : 'null'})"`)
            : `onclick="openLightbox('${post.image_url}', '${promptParam}')"`;
        // 이미지 HTML
        const imageHtml = post.image_url
            ? `<div class="feed-image-container"><img src="${post.image_url}" class="feed-image" loading="lazy" ${imageClick}></div>`
            : "";

        // 댓글 HTML (숨김 처리)
        const commentsHtml = post.comments.map(c => `
            <div class="feed-comment-item">
                <div class="comment-author-row">
                    <img src="${c.author_image || 'https://via.placeholder.com/20'}" class="comment-avatar" style="${c.author_type === 'persona' ? 'cursor:pointer;' : ''}" ${c.author_type === 'persona' ? `onclick="openEveProfileFromFeed(${c.author_id}, ${c.room_id ? c.room_id : 'null'})"` : ""}>
                    <span class="comment-username" style="${c.author_type === 'persona' ? 'cursor:pointer;' : ''}" ${c.author_type === 'persona' ? `onclick="openEveProfileFromFeed(${c.author_id}, ${c.room_id ? c.room_id : 'null'})"` : ""}>${c.author_name}</span>
                    <span class="comment-time">${c.created_at.split(" ")[1] || ""}</span>
                    ${c.can_delete ? `<button class="feed-comment-del-btn" onclick="deleteFeedComment(${c.id})" style="margin-left:auto; border:none; background:transparent; color:#ff3b30; font-size:11px; cursor:pointer;">삭제</button>` : ""}
                </div>
                <div class="comment-content">${c.content}</div>
            </div>
        `).join("");

        const taggedPersonas = Array.isArray(post.tagged_personas) ? post.tagged_personas : [];
        const tagActivity = (post.tag_activity || "").trim();
        const tagsHtml = taggedPersonas.length
            ? `<div class="feed-tag-row">
                <span class="feed-tag-activity">${tagActivity || '함께한 이브'}</span>
                ${taggedPersonas.map(t => {
                const click = t.persona_id
                    ? `onclick="event.stopPropagation();openEveProfileFromFeed(${t.persona_id}, ${t.room_id ? t.room_id : 'null'})"`
                    : "";
                return `<span class="feed-tag-chip" ${click}>@${t.name}</span>`;
            }).join("")}
            </div>`
            : "";
        const locationLabel = post.location_name
            ? `${post.location_district ? `${post.location_district} · ` : ""}${post.location_name}`
            : "";

        return `
        <article class="feed-card">
            <!-- 좌측: 아바타 -->
            <div class="feed-avatar-col" ${profileClick}>
                <div class="feed-avatar">
                   <img src="${post.author_image || 'https://via.placeholder.com/32'}" onerror="this.style.display='none'">
                   ${!post.author_image ? post.author_name[0] : ''}
                </div>
            </div>
            
            <!-- 우측: 콘텐츠 -->
            <div class="feed-content-col">
                <div class="feed-header-row">
                    <div class="feed-username" ${profileClick}>${post.author_name}</div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <div class="feed-time">${post.created_at}${locationLabel ? ` · ${locationLabel}` : ""}</div>
                        ${post.can_delete ? `<button onclick="deleteFeedPost(${post.id})" style="border:none; background:transparent; color:#ff3b30; font-size:12px; cursor:pointer;">삭제</button>` : ""}
                    </div>
                </div>
                
                <div class="feed-text">${post.content}</div>
                ${tagsHtml}
                
                ${imageHtml}
                
                <div class="feed-actions">
                    <button class="feed-action-btn" onclick="toggleFeedLike(${post.id}, this)">
                        <span>${post.has_liked ? '❤️' : '🤍'}</span> 
                        <span class="feed-count" style="${post.like_count > 0 ? '' : 'display:none'}">${post.like_count}</span>
                    </button>
                    <button class="feed-action-btn" onclick="toggleComments(${post.id})">
                        <span>💬</span>
                        <span class="feed-count" style="${post.comments.length > 0 ? '' : 'display:none'}">${post.comments.length}</span>
                    </button>
                    <button class="feed-action-btn" ${dmClick} ${canOpenPersonaProfile ? "" : "disabled"} style="${canOpenPersonaProfile ? "" : "opacity:0.45; cursor:not-allowed;"}">
                        <span>💌</span>
                    </button>
                </div>

                <!-- 댓글 영역 (토글) -->
                <div id="comments-${post.id}" class="feed-comments-section" style="display: none;">
                    ${commentsHtml}
                    <!-- [Phase 4] 유저 댓글 입력창 -->
                    <div class="feed-comment-input-row" style="display:flex; margin-top:10px; gap:8px;">
                        <input type="text" id="feed-comment-input-${post.id}" placeholder="댓글 달기..." style="flex:1; border:1px solid var(--border-color); border-radius:20px; padding:8px 12px; font-size:13px; background:var(--bg-card); color:var(--text-main);">
                        <button onclick="postFeedComment(${post.id})" style="background:#F0457A; color:white; border:none; border-radius:20px; padding:0 15px; font-size:13px; font-weight:600; cursor:pointer;">게시</button>
                    </div>
                </div>
            </div>
        </article>`;
    }).join("");

    if (append) {
        container.insertAdjacentHTML("beforeend", html);
    } else {
        container.innerHTML = html;
    }
}

// [v2.0.0] 댓글 토글
function toggleComments(postId) {
    const el = document.getElementById(`comments-${postId}`);
    if (el) {
        el.style.display = el.style.display === "none" ? "block" : "none";
    }
}

// [Phase 4] 피드 액션 연동
async function toggleFeedLike(postId, btnElement) {
    if (!accessToken) return showAuthModal();
    try {
        const res = await fetch(`/api/feed/${postId}/like`, {
            method: "POST",
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        if (res.ok) {
            const data = await res.json();
            const countSpan = btnElement.querySelector('.feed-count');
            countSpan.textContent = data.like_count;
            countSpan.style.display = data.like_count > 0 ? '' : 'display:none';
            // Scale animation for feedback
            const iconSpan = btnElement.querySelector('span:first-child');
            if (data.has_liked) {
                iconSpan.textContent = '❤️';
                iconSpan.classList.add('heart-bounce');
                setTimeout(() => iconSpan.classList.remove('heart-bounce'), 300);
            } else {
                iconSpan.textContent = '🤍';
            }
        }
    } catch (e) { console.error("Like failed", e); }
}

async function postFeedComment(postId) {
    if (!accessToken) return showAuthModal();
    const inputEl = document.getElementById(`feed-comment-input-${postId}`);
    const content = inputEl.value.trim();
    if (!content) return;

    try {
        const res = await fetch(`/api/feed/${postId}/comment`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
            body: JSON.stringify({ content })
        });
        if (res.ok) {
            inputEl.value = "";
            loadFeed(true); // 댓글 등록 완료 후 전체 피드 새로고침
        } else {
            alert("댓글 등록에 실패했습니다.");
        }
    } catch (e) {
        console.error("Comment post failed", e);
    }
}

async function deleteFeedPost(postId) {
    if (!accessToken) return showAuthModal();
    if (!confirm("이 피드를 삭제할까요?")) return;
    try {
        const res = await fetch(`/api/feed/${postId}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        if (!res.ok) {
            let msg = "피드 삭제 실패";
            try {
                const err = await res.json();
                msg = err.detail || msg;
            } catch (_) { }
            throw new Error(msg);
        }
        await loadFeed(true);
    } catch (e) {
        alert(e.message || "피드 삭제 실패");
    }
}

async function deleteFeedComment(commentId) {
    if (!accessToken) return showAuthModal();
    if (!confirm("이 댓글을 삭제할까요?")) return;
    try {
        const res = await fetch(`/api/feed/comment/${commentId}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        if (!res.ok) {
            let msg = "댓글 삭제 실패";
            try {
                const err = await res.json();
                msg = err.detail || msg;
            } catch (_) { }
            throw new Error(msg);
        }
        await loadFeed(true);
    } catch (e) {
        alert(e.message || "댓글 삭제 실패");
    }
}

// [v2.0.0] World Map Logic
let mapDataCache = null;

async function loadMap() {
    try {
        const res = await fetch("/api/map", {
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        if (res.ok) {
            const data = await res.json();
            mapDataCache = data;
            renderMap(data);
        }
    } catch (e) {
        console.error("Map Load Error:", e);
    }
}

function renderMap(data) {
    const grid = document.getElementById("world-map-grid");
    if (!grid) return;

    // District 순서 고정 (UI 배치)
    const districtOrder = ["루미나 시티", "네온 디스트릭트", "세렌 밸리", "에코 베이", "더 하이브"];

    // 데이터 매핑
    grid.innerHTML = districtOrder.map(dName => {
        const district = data.districts.find(d => d.name === dName);
        if (!district) return "";

        // 이 구역에 있는 내 친구들 찾기
        const friendsHere = data.friends.filter(f => f.district === dName);

        // 핫플레이스 태그 (인구 많은 순 2개)
        const hotSpots = district.locations
            .sort((a, b) => b.pop - a.pop)
            .slice(0, 3)
            .map(loc => `
                <div class="location-tag ${loc.pop > 5 ? 'hot' : ''}">
                    ${loc.name} <span style="opacity:0.6; margin-left:4px;">${loc.pop}</span>
                </div>
            `).join("");

        const friendsHtml = friendsHere.length > 0 ? `
            <div class="map-friends-row">
                ${friendsHere.map(f => `
                     <img src="${f.image || 'https://via.placeholder.com/28'}" 
                          class="map-friend-avatar" 
                          title="${f.name} - ${f.location_name}"
                          onclick="openProfile(${f.room_id}); event.stopPropagation();">
                `).join("")}
            </div>
        ` : "";

        return `
            <div class="district-card" data-theme="${dName}" onclick="openMapDistrictModal('${dName}')">
                <div class="district-header">
                    <div class="district-info">
                        <div class="district-name">${dName}</div>
                        <div style="font-size:11px; opacity:0.7; margin-top:2px;">
                            ${getDistrictDesc(dName)}
                        </div>
                    </div>
                    <div class="district-pop">👥 ${district.total_pop}</div>
                </div>
                
                <div class="location-list">
                    ${hotSpots}
                </div>
                
                ${friendsHtml}
            </div>
        `;
    }).join("");
}

function closeMapDistrictModal() {
    const overlay = document.getElementById("map-district-overlay");
    if (overlay) overlay.style.display = "none";
}

function openMapDistrictModal(districtName) {
    const overlay = document.getElementById("map-district-overlay");
    const title = document.getElementById("map-district-title");
    const list = document.getElementById("map-district-eves");
    if (!overlay || !title || !list) return;

    title.innerText = `${districtName} · 현재 이브`;
    const eves = (mapDataCache?.district_eves || []).filter(e => e.district === districtName);
    if (eves.length === 0) {
        list.innerHTML = `<div style="grid-column:1/-1; font-size:13px; color:var(--text-sub);">현재 이 지역에 이브가 없습니다.</div>`;
    } else {
        list.innerHTML = eves.map(e => `
            <button type="button"
                onclick="openMapEveFullProfile(${e.id}); event.stopPropagation();"
                style="border:none; background:transparent; padding:0; cursor:pointer; text-align:center;">
                <img src="${e.image || 'https://via.placeholder.com/60'}"
                    alt="${e.name}"
                    style="width:56px; height:56px; border-radius:50%; object-fit:cover; border:1px solid var(--border); display:block; margin:0 auto 6px;">
                <div style="font-size:11px; color:var(--text-main); font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${e.name}</div>
            </button>
        `).join("");
    }

    overlay.style.display = "flex";
}

function openMapEveFullProfile(personaId) {
    const eve = (mapDataCache?.district_eves || []).find(e => e.id === personaId);
    if (!eve) return;
    closeMapDistrictModal();

    if (eve.room_id) {
        openProfile(eve.room_id);
        return;
    }

    fetch(`/api/public/persona/${eve.id}`, {
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
    })
        .then((r) => (r.ok ? r.json() : null))
        .then((p) => {
            renderFullProfile({
                room_id: null,
                persona_id: eve.id,
                name: eve.name,
                age: eve.age,
                gender: eve.gender,
                mbti: eve.mbti,
                profile_image_url: eve.image,
                profile_images: Array.isArray(eve.profile_images) ? eve.profile_images : [],
                image_prompt: eve.image_prompt,
                profile_details: eve.profile_details || {},
                diaries: Array.isArray(p?.diaries) ? p.diaries : [],
                relationship_category: "Not friends",
                p_seriousness: null,
                p_friendliness: null,
                p_rationality: null,
                p_slang: null,
                v_likeability: null,
                v_v_mood: null,
                v_relationship: null,
                v_erotic: null
            });
        })
        .catch(() => {
            renderFullProfile({
                room_id: null,
                persona_id: eve.id,
                name: eve.name,
                age: eve.age,
                gender: eve.gender,
                mbti: eve.mbti,
                profile_image_url: eve.image,
                profile_images: Array.isArray(eve.profile_images) ? eve.profile_images : [],
                image_prompt: eve.image_prompt,
                profile_details: eve.profile_details || {},
                diaries: [],
                relationship_category: "Not friends",
                p_seriousness: null,
                p_friendliness: null,
                p_rationality: null,
                p_slang: null,
                v_likeability: null,
                v_v_mood: null,
                v_relationship: null,
                v_erotic: null
            });
        });

    const chatBtn = document.getElementById("fp-chat-btn");
    const delBtn = document.getElementById("fp-delete-btn");
    const relBtn = document.getElementById("fp-rel-btn");
    const lifeBtn = document.getElementById("fp-life-btn");

    if (chatBtn) {
        chatBtn.innerText = "Add Friend";
        chatBtn.onclick = () => {
            openMiniProfile(eve.id, null);
            closeProfile();
        };
    }
    if (delBtn) delBtn.style.display = "none";
    if (relBtn) relBtn.style.display = "none";
    if (lifeBtn) lifeBtn.style.display = "none";

    document.body.classList.add("is-profile");
    const profilePage = document.getElementById("profile-page");
    if (profilePage) profilePage.scrollTop = 0;
}

function getDistrictDesc(name) {
    const desc = {
        "루미나 시티": "비즈니스 & 트렌드",
        "세렌 밸리": "자연 & 힐링",
        "에코 베이": "문화 & 예술",
        "더 하이브": "평온한 거주 구역",
        "네온 디스트릭트": "나이트라이프 & 파티"
    };
    return desc[name] || "";
}

// [Phase 5] DM 버튼 클릭 핸들러 (피드)
function handleFeedDMClick(authorId, roomId) {
    if (!accessToken) return showAuthModal();
    if (roomId) {
        joinRoom(roomId);
    } else {
        openMiniProfile(authorId, null);
    }
}

// 초기 실행 + 피드 2분 자동 갱신
checkAuth();
setInterval(() => {
    if (currentView === "feed") {
        loadFeed(true);
    }
}, 120000);
// 피드 탭이 아닐 때는 배지만 더 자주 갱신
setInterval(() => {
    if (currentView !== "feed") {
        pollFeedBadge();
    }
}, 30000);
