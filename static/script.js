
let globalSockets = {}; // 룸 아이디별 웹소켓 관리
let currentRoomId = null;
let friendsData = [];
let messageQueue = [];
let unreadCounts = {}; // 안 읽은 메시지 함
let isProcessingQueue = false;
let currentView = "list";
let notiTargetRoomId = null;
const DEV_PASS = "31313142";

// [v1.4.2 신규] 관리자 제어 대상 상태 관리
let adminSelectedRoomId = null;

// 계정 상태 관리 변수
let accessToken = localStorage.getItem("accessToken");
let isAdmin = localStorage.getItem("isAdmin") === "true";
let currentUsername = localStorage.getItem("username");

// 앱 시작 시 인증 체크
async function checkAuth() {
    const authOverlay = document.getElementById("auth-overlay");
    if (!accessToken) {
        authOverlay.style.display = "flex";
    } else {
        authOverlay.style.display = "none";
        updateUIByAuth();
        loadFriends();
    }
}

// 로그인/회원가입 UI 전환
function toggleAuthMode(mode) {
    document.getElementById("login-form").style.display =
        mode === "login" ? "flex" : "none";
    document.getElementById("register-form").style.display =
        mode === "register" ? "flex" : "none";
}

// 로그인 처리
async function handleLogin() {
    const u = document.getElementById("login-username").value;
    const p = document.getElementById("login-password").value;

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

            document.getElementById("auth-overlay").style.display = "none";

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
        alert("서버 연결에 실패했습니다.");
    }
}

// 회원가입 처리 - [v1.5.0] 회원가입 후 자동 로그인 및 프로필 작성
async function handleRegister() {
    const u = document.getElementById("reg-username").value;
    const p = document.getElementById("reg-password").value;

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
    localStorage.clear();
    location.reload();
}

// 로그아웃 버튼 핸들러
function handleLogout() {
    if (confirm("로그아웃 하시겠습니까?")) {
        logout();
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

function switchMobileView(view) {
    currentView = view;
    document.body.classList.remove("is-chatting", "is-dev", "show-auth");
    const navs = document.querySelectorAll(".nav-item");
    navs.forEach((n) => n.classList.remove("active"));

    const title = document.getElementById("tab-title");
    const slider = document.getElementById("tab-slider");

    // [v1.5.0] 모든 탭 숨기기
    const friendTab = document.getElementById("friend-tab-view");
    const chatTab = document.getElementById("chat-tab-view");
    const settingsTab = document.getElementById("settings-tab-view");
    if (friendTab) friendTab.style.display = "none";
    if (chatTab) chatTab.style.display = "none";
    if (settingsTab) settingsTab.style.display = "none";

    if (view === "list") {
        if (slider) slider.style.transform = "translateX(0%)";
        if (friendTab) friendTab.style.display = "flex";
        title.innerText = "친구";
        navs[0].classList.add("active");
        renderFriendList();
    } else if (view === "chats") {
        if (slider) slider.style.transform = "translateX(-50%)";
        if (chatTab) chatTab.style.display = "flex";
        title.innerText = "대화";
        navs[1].classList.add("active");
        renderChatList();
    } else if (view === "settings") {
        // [v1.5.0] 설정 탭
        if (settingsTab) settingsTab.style.display = "flex";
        title.innerText = "설정";
        navs[2].classList.add("active");
        loadSettings();
    } else if (view === "chat") {
        document.body.classList.add("is-chatting");
    } else if (view === "dev") {
        document.body.classList.add("is-dev");
        if (navs[3]) navs[3].classList.add("active");
        switchAdminTab("status");
    }
}

// [v1.4.2 수정] 관리자 상위 탭 전환 (3분할 탭 연동)
function switchAdminTab(tab) {
    const views = {
        "status": document.getElementById("admin-status-view"),
        "users": document.getElementById("admin-user-view"),
        "notice": document.getElementById("admin-notice-view")
    };
    const detailView = document.getElementById("admin-detail-view");

    Object.values(views).forEach(v => { if (v) v.style.display = "none"; });
    if (detailView) detailView.style.display = "none";

    if (views[tab]) views[tab].style.display = "block";

    const atabStatus = document.getElementById("atab-status");
    const atabUsers = document.getElementById("atab-users");
    const atabNotice = document.getElementById("atab-notice");

    if (atabStatus) atabStatus.classList.toggle("active", tab === "status");
    if (atabUsers) atabUsers.classList.toggle("active", tab === "users");
    if (atabNotice) atabNotice.classList.toggle("active", tab === "notice");

    if (tab === "status") backToEveBrowser();
    if (tab === "users") loadAdminUsers();
}

// [v1.4.2 신규] 관리자 STATUS 서브 탭 전환
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
}

// [v1.4.2 신규] 모든 이브 목록(World EVE Browser) 로드
async function loadAdminEves() {
    const res = await fetch("/admin/eves", {
        headers: { Authorization: `Bearer ${accessToken}` }
    });
    if (res.ok) {
        const tree = await res.json();
        const list = document.getElementById("admin-eve-tree-list");
        list.innerHTML = tree.map(user => `
            <div class="user-group-card">
                <div class="user-group-header">${user.username} (${user.rooms.length})</div>
                ${user.rooms.map(room => `
                    <div class="eve-mini-item" onclick="inspectEve(${room.room_id}, '${room.persona_name}')">
                        <div class="info">
                            <span class="name">${room.persona_name}</span>
                            <span class="mbti">${room.mbti}</span>
                        </div>
                        <div class="indicator ${room.is_active ? '' : 'offline'}">
                            ${room.is_active ? '● LIVE' : '○ IDLE'}
                        </div>
                    </div>
                `).join('')}
            </div>
        `).join('');
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
        renderFriendList();
        renderChatList();
    } catch (e) {
        setEngineStatus(false);
    }
}

function renderFriendList() {
    const list = document.getElementById("friend-list");
    if (!list) return;
    list.innerHTML = friendsData
        .map((f) => {
            const colors = ["#FF9500", "#FF2D55", "#AF52DE", "#5AC8FA", "#34C759"];
            const color = colors[f.name.charCodeAt(0) % 5];
            const isOnline = f.status === "online";
            const avatarContent = f.profile_image_url
                ? `<img src="${f.profile_image_url}" class="avatar-img">`
                : f.name[0];

            // [v1.7.0] 동적 상태 메시지: 일정을 기반으로 활동 유추
            let statusActivity = "LUXID 체류 중";
            if (isOnline) statusActivity = "PORTAL 접속 중";

            // [v1.7.2] 신규 형식 (object) 및 구형식 (array) 모두 지원
            if (f.daily_schedule) {
                const hour = new Date().getHours();

                // 신규 형식: {wake_time, daily_tasks, sleep_time}
                if (typeof f.daily_schedule === 'object' && !Array.isArray(f.daily_schedule)) {
                    const wake = parseInt((f.daily_schedule.wake_time || "07:00").split(":")[0]);
                    const sleep = parseInt((f.daily_schedule.sleep_time || "23:00").split(":")[0]);

                    if (hour < wake || hour >= sleep) {
                        statusActivity = "휴식 중";
                    } else {
                        statusActivity = "활동 중";
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
                    if (nowActivity) statusActivity = nowActivity.activity;
                }
            }

            return `
            <div class="friend-item portal-card ${isOnline ? "is-online" : ""}" onclick="openProfile(${f.room_id})">
                <div class="friend-avatar" style="background:${f.profile_image_url ? "none" : color}">
                    ${avatarContent}
                    <div class="online-dot"></div>
                </div>
                <div class="friend-info">
                    <div class="name-row"><span class="name">${f.name}</span><span class="mbti">${f.mbti}</span></div>
                    <div class="last-msg">${statusActivity}</div>
                </div>
            </div>
        `;
        })
        .join("");
}

function renderChatList() {
    const list = document.getElementById("chat-list");
    if (!list) return;
    const chattingFriends = friendsData.filter((f) => f.history && f.history.length > 0);

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

function openProfile(roomId) {
    const f = friendsData.find((item) => item.room_id === roomId);
    if (!f) return;

    const overlay = document.getElementById("profile-overlay");
    const avatar = document.getElementById("profile-avatar");
    const name = document.getElementById("profile-name");
    const mbti = document.getElementById("profile-mbti");
    const hook = document.getElementById("profile-hook");
    const intro = document.getElementById("profile-intro");
    const job = document.getElementById("profile-job");
    const goal = document.getElementById("profile-goal");
    const lifestyle = document.getElementById("profile-lifestyle");
    const tmi = document.getElementById("profile-tmi");
    const interestsArea = document.getElementById("profile-interests");
    const chatBtn = document.getElementById("profile-chat-btn");
    const delBtn = document.getElementById("profile-delete-btn");

    const colors = ["#FF9500", "#FF2D55", "#AF52DE", "#5AC8FA", "#34C759"];

    if (f.profile_image_url) {
        avatar.style.background = "none";
        // [v1.7.0] 이미지 클릭 시 라이트박스 오픈
        avatar.innerHTML = `<img src="${f.profile_image_url}" class="avatar-img-large" onclick="openLightbox('${f.profile_image_url}', '${(f.image_prompt || "").replace(/'/g, "\\'")}'); event.stopPropagation();">`;
    } else {
        avatar.style.background = colors[f.name.charCodeAt(0) % 5];
        avatar.innerText = f.name[0];
    }

    name.innerText = f.name;
    mbti.innerText = f.mbti;

    const details = f.profile_details || {};
    hook.innerText = details.hook || "루시드에서 당신을 기다려요.";
    intro.innerText = details.intro || "안녕하세요, LUXID에 살고 있어요.";
    job.innerText = details.job || "-";
    goal.innerText = details.goal || "-";
    lifestyle.innerText = details.lifestyle || "-";
    tmi.innerText = details.tmi || "-";

    interestsArea.innerHTML = (details.interests || [])
        .map((i) => `<span class="interest-tag">${i}</span>`)
        .join("");

    chatBtn.onclick = () => {
        closeProfile();
        joinRoom(roomId);
    };

    delBtn.onclick = (e) => {
        closeProfile();
        deleteFriend(e, roomId);
    };

    // [v1.7.0] 관계 분석 버튼 핸들러
    const relBtn = document.getElementById("profile-rel-btn");
    if (relBtn) {
        relBtn.onclick = () => {
            // closeProfile(); // 프로필 유지하고 위에 띄울지, 닫고 띄울지 결정. 여기선 위로 덮도록 함.
            openRelationshipView(roomId);
        };
    }

    // [v1.7.1] Life Details 버튼 핸들러 (관리자 전용)
    const lifeBtn = document.getElementById("profile-life-btn");
    if (lifeBtn) {
        if (isAdmin) {
            lifeBtn.style.display = "block";
            lifeBtn.onclick = () => {
                openLifeDetails(roomId);
            };
        } else {
            lifeBtn.style.display = "none";
        }
    }

    overlay.style.display = "flex";
}

function closeProfile() {
    const overlay = document.getElementById("profile-overlay");
    if (overlay) overlay.style.display = "none";
}

// [v1.7.0] 이미지 라이트박스 제어
function openLightbox(url, promptText = null) {
    const overlay = document.getElementById("lightbox-overlay");
    const img = document.getElementById("lightbox-img");
    const promptEl = document.getElementById("lightbox-prompt");
    if (overlay && img) {
        img.src = url;

        if (isAdmin && promptText) {
            promptEl.innerText = `[IMAGE PROMPT]\n${promptText}`;
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
    closeProfile();
    switchMobileView("chat");

    const f = friendsData.find((item) => item.room_id === roomId);
    if (!f) return;

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
    if (confirm("이 친구를 삭제하시겠습니까?")) {
        const res = await fetch(`/delete-friend/${roomId}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (res.ok) {
            friendsData = friendsData.filter((item) => item.room_id !== roomId);
            renderFriendList();
            renderChatList();
        }
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
        if (schedule) schedule.value = JSON.stringify(p.daily_schedule || [], null, 2);
        if (details) details.value = JSON.stringify(p.profile_details || {}, null, 2);
        if (model) model.value = data.model_id || "gemini-3-flash-preview";
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
    if (accessToken) {
        if (userDisplay) userDisplay.innerText = currentUsername || "";
        if (isAdmin) {
            document.body.classList.add("dev-unlocked");
            if (navDev) navDev.style.display = "flex";
        } else {
            if (navDev) navDev.style.display = "none";
        }
    }
}

// =================================================
// [v1.5.0] 온보딩 플로우 함수
// =================================================

// 시작하기 버튼 클릭 시 로그인/회원가입 폼 표시
function showAuthForms() {
    const welcomeScreen = document.getElementById("welcome-screen");
    const authFormContainer = document.getElementById("auth-form-container");

    if (welcomeScreen) welcomeScreen.style.display = "none";
    if (authFormContainer) authFormContainer.style.display = "block";
}

// =================================================
// [v1.5.0] 프로필 작성 함수
// =================================================

// 프로필 이미지 미리보기
function previewProfileImage() {
    const fileInput = document.getElementById("profile-image-input");
    const preview = document.getElementById("preview-avatar");

    if (fileInput.files && fileInput.files[0]) {
        const reader = new FileReader();
        reader.onload = function (e) {
            preview.style.background = "none";
            preview.innerHTML = `<img src="${e.target.result}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">`;
        };
        reader.readAsDataURL(fileInput.files[0]);
    }
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
    }
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
            mbti: document.getElementById("profile-mbti").value || null,
            profile_details: {
                intro: document.getElementById("profile-intro").value || "",
                job: document.getElementById("profile-job").value || "",
                goal: document.getElementById("profile-goal").value || "",
                lifestyle: document.getElementById("profile-lifestyle").value || "",
                interests: document.getElementById("profile-interests").value ? document.getElementById("profile-interests").value.split(",").map(s => s.trim()).filter(s => s) : [],
                tmi: document.getElementById("profile-tmi").value || ""
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
                    const reader = new FileReader();
                    reader.onload = async function (e) {
                        const imgRes = await fetch("/api/user/profile/image", {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "Authorization": `Bearer ${accessToken}`
                            },
                            body: JSON.stringify({ image_url: e.target.result })
                        });
                        if (!imgRes.ok) console.error("Image upload failed");
                    };
                    reader.readAsDataURL(fileInput.files[0]);
                    // 이미지 업로드는 비동기로 진행되므로 약간의 지연 후 완료 처리
                    await new Promise(r => setTimeout(r, 1000));
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
            if (profile.mbti) document.getElementById("profile-mbti").value = profile.mbti;

            if (profile.profile_details) {
                if (profile.profile_details.intro) document.getElementById("profile-intro").value = profile.profile_details.intro;
                if (profile.profile_details.job) document.getElementById("profile-job").value = profile.profile_details.job;
                if (profile.profile_details.goal) document.getElementById("profile-goal").value = profile.profile_details.goal;
                if (profile.profile_details.lifestyle) document.getElementById("profile-lifestyle").value = profile.profile_details.lifestyle;
                if (profile.profile_details.interests) document.getElementById("profile-interests").value = profile.profile_details.interests.join(", ");
                if (profile.profile_details.tmi) document.getElementById("profile-tmi").value = profile.profile_details.tmi;
            }

            // 아바타 미리보기
            if (profile.profile_image_url) {
                const preview = document.getElementById("preview-avatar");
                preview.style.background = "none";
                preview.innerHTML = `<img src="${profile.profile_image_url}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">`;
            } else {
                generateDefaultAvatar(profile.display_name || currentUsername);
            }

            // 프로필 작성 화면 표시
            document.getElementById("profile-setup-overlay").style.display = "flex";
        }
    } catch (e) {
        alert("프로필 로드에 실패했습니다.");
    }
}

// 초기 실행
checkAuth();
switchMobileView("list");