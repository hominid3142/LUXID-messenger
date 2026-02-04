let globalSockets = {}; // 룸 아이디별 웹소켓 관리
let currentRoomId = null;
let friendsData = [];
let messageQueue = [];
let unreadCounts = {}; // 안 읽은 메시지 함
let isProcessingQueue = false;
let currentView = "list";
let notiTargetRoomId = null;
const DEV_PASS = "31313142";

// [신규] 계정 상태 관리 변수
let accessToken = localStorage.getItem("accessToken");
let isAdmin = localStorage.getItem("isAdmin") === "true";
let currentUsername = localStorage.getItem("username");

// [신규] 앱 시작 시 인증 체크
async function checkAuth() {
    const authOverlay = document.getElementById("auth-overlay");
    if (!accessToken) {
        authOverlay.style.display = "flex";
    } else {
        authOverlay.style.display = "none";
        updateUIByAuth(); // UI 업데이트 추가
        loadFriends();
    }
}

// [신규] 로그인/회원가입 UI 전환
function toggleAuthMode(mode) {
    document.getElementById("login-form").style.display =
        mode === "login" ? "flex" : "none";
    document.getElementById("register-form").style.display =
        mode === "register" ? "flex" : "none";
}

// [신규] 로그인 처리
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
            updateUIByAuth(); // UI 업데이트 추가
            loadFriends();
        } else {
            const err = await res.json();
            alert(err.detail || "로그인에 실패했습니다.");
        }
    } catch (e) {
        alert("서버 연결에 실패했습니다.");
    }
}

// [신규] 회원가입 처리
async function handleRegister() {
    const u = document.getElementById("reg-username").value;
    const p = document.getElementById("reg-password").value;

    const res = await fetch("/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: p }),
    });

    if (res.ok) {
        alert("가입되었습니다. 로그인해주세요.");
        toggleAuthMode("login");
    } else {
        const err = await res.json();
        alert(err.detail || "가입에 실패했습니다.");
    }
}

// [신규] 로그아웃 (인증 만료 시 내부 자동 호출용)
function logout() {
    localStorage.clear();
    location.reload();
}

// [신규] 로그아웃 버튼 핸들러 (사용자가 직접 클릭 시 호출)
function handleLogout() {
    if (confirm("로그아웃 하시겠습니까?")) {
        logout();
    }
}

function handleDevBtn() {
    const isUnlocked = document.body.classList.contains("dev-unlocked");
    if (isUnlocked) {
        // Lock 처리
        document.body.classList.remove("dev-unlocked", "is-dev");
        document.getElementById("dev-btn").innerText = "DEV";
        switchMobileView("list");
    } else {
        // [수정] 관리자 계정일 경우 비번 입력 없이 바로 잠금 해제
        if (isAdmin) {
            document.body.classList.remove("dev-locked");
            document.body.classList.add("dev-unlocked");
            document.getElementById("dev-btn").innerText = "DEV OFF";
        } else {
            // 일반 계정일 경우에만 암호창 띄우기
            document.body.classList.add("show-auth");
        }
    }
}

function checkAuthPassword() {
    // 기존 checkAuth와 이름 충돌 방지를 위해 변경
    const input = document.getElementById("pass-input");
    if (input.value === DEV_PASS) {
        document.body.classList.remove("dev-locked", "show-auth");
        document.body.classList.add("dev-unlocked");
        document.getElementById("dev-btn").innerText = "DEV OFF";
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

    if (view === "list") {
        if (slider) slider.style.transform = "translateX(0%)";
        title.innerText = "친구";
        navs[0].classList.add("active");
        renderFriendList();
    } else if (view === "chats") {
        if (slider) slider.style.transform = "translateX(-50%)";
        title.innerText = "대화";
        navs[1].classList.add("active");
        renderChatList();
    } else if (view === "chat") {
        document.body.classList.add("is-chatting");
    } else if (view === "dev") {
        document.body.classList.add("is-dev");
        document.getElementById("nav-dev").classList.add("active");
        // [신규] 개발자 탭 진입 시 기본 탭 설정
        switchAdminTab("status");
    }
}

// [신규] 관리자 내부 탭 전환 로직
function switchAdminTab(tab) {
    const statusView = document.getElementById("admin-status-view");
    const userView = document.getElementById("admin-user-view");
    const detailView = document.getElementById("admin-detail-view");
    const btnStatus = document.getElementById("atab-status");
    const btnUsers = document.getElementById("atab-users");

    statusView.style.display = tab === "status" ? "block" : "none";
    userView.style.display = tab === "users" ? "block" : "none";
    detailView.style.display = "none";

    btnStatus.classList.toggle("active", tab === "status");
    btnUsers.classList.toggle("active", tab === "users");

    if (tab === "users") loadAdminUsers();
}

async function loadFriends() {
    try {
        // [수정] 인증 헤더 추가
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

// [수정] 친구 탭 전용 디자인 (portal-card) 적용 및 프로필 연결
function renderFriendList() {
    const list = document.getElementById("friend-list");
    list.innerHTML = friendsData
        .map((f) => {
            const colors = [
                "#FF9500",
                "#FF2D55",
                "#AF52DE",
                "#5AC8FA",
                "#34C759",
            ];
            const color = colors[f.name.charCodeAt(0) % 5];
            const isOnline = f.status === "online";
            return `
            <div class="friend-item portal-card ${isOnline ? "is-online" : ""}" onclick="openProfile(${f.room_id})">
                <div class="friend-avatar" style="background:${color}">
                    ${f.name[0]}
                    <div class="online-dot"></div>
                </div>
                <div class="friend-info">
                    <div class="name-row"><span class="name">${f.name}</span><span class="mbti">${f.mbti}</span></div>
                    <div class="last-msg">${isOnline ? "PORTAL 접속 중" : "LUXID 체류 중"}</div>
                </div>
            </div>
        `;
        })
        .join("");
}

function renderChatList() {
    const list = document.getElementById("chat-list");
    const chattingFriends = friendsData.filter(
        (f) => f.history && f.history.length > 0,
    );

    list.innerHTML = chattingFriends
        .map((f) => {
            const lastMsg = f.history[f.history.length - 1].content;
            const colors = [
                "#FF9500",
                "#FF2D55",
                "#AF52DE",
                "#5AC8FA",
                "#34C759",
            ];
            const color = colors[f.name.charCodeAt(0) % 5];
            const unread = unreadCounts[f.room_id] || 0;

            return `
            <div class="friend-item ${unread > 0 ? "has-unread" : ""}" onclick="joinRoom(${f.room_id})">
                <div class="friend-avatar" style="background:${color}">${f.name[0]}</div>
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

// [신규] 프로필 열기 로직
function openProfile(roomId) {
    const f = friendsData.find((item) => item.room_id === roomId);
    if (!f) return;

    const overlay = document.getElementById("profile-overlay");
    const avatar = document.getElementById("profile-avatar");
    const name = document.getElementById("profile-name");
    const mbti = document.getElementById("profile-mbti");
    const chatBtn = document.getElementById("profile-chat-btn");
    const delBtn = document.getElementById("profile-delete-btn");

    const colors = ["#FF9500", "#FF2D55", "#AF52DE", "#5AC8FA", "#34C759"];
    avatar.style.background = colors[f.name.charCodeAt(0) % 5];
    avatar.innerText = f.name[0];
    name.innerText = f.name;
    mbti.innerText = f.mbti;

    chatBtn.onclick = () => {
        closeProfile();
        joinRoom(roomId);
    };

    delBtn.onclick = (e) => {
        closeProfile();
        deleteFriend(e, roomId);
    };

    overlay.style.display = "flex";
}

// [신규] 프로필 닫기
function closeProfile() {
    document.getElementById("profile-overlay").style.display = "none";
}

function initSocket(roomId) {
    // 기존 연결이 있다면 유지
    if (globalSockets[roomId]) return;

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    // [수정] 쿼리 파라미터로 토큰 전달 (WebSocket 인증용)
    const ws = new WebSocket(
        `${protocol}//${location.host}/ws/chat/${roomId}?token=${accessToken}`,
    );

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

function handleIncomingData(roomId, data) {
    const f = friendsData.find((item) => item.room_id === roomId);
    if (!f) return;

    // 1. 상태 및 헤더 실시간 업데이트
    const msgStatus =
        data.status || (data.current_status && data.current_status.status);
    if (msgStatus) {
        f.status = msgStatus;
        if (currentView === "list") renderFriendList();

        if (roomId === currentRoomId) {
            const statusEl = document.getElementById("active-friend-status");
            statusEl.innerText = msgStatus === "online" ? "온라인" : "오프라인";
            statusEl.className = `status ${msgStatus}`;
        }
    }

    // 2. Typing 상태 처리
    if (roomId === currentRoomId && typeof data.typing !== "undefined") {
        const statusEl = document.getElementById("active-friend-status");
        if (data.typing) {
            statusEl.innerText = "입력 중...";
            statusEl.classList.add("typing");
        } else {
            statusEl.innerText = f.status === "online" ? "온라인" : "오프라인";
            statusEl.classList.remove("typing");
        }
    }

    // 3. 메시지 처리
    if (data.responses) {
        data.responses.forEach((r) => {
            const msgObj = {
                role: "assistant",
                content: r.text,
                ts: r.ts,
            };
            if (!f.history) f.history = [];
            f.history.push(msgObj);

            if (roomId === currentRoomId && currentView === "chat") {
                appendMsg("ai", r.text, r.ts);
            } else {
                unreadCounts[roomId] = (unreadCounts[roomId] || 0) + 1;
                showInAppNoti(f, r.text, roomId);
                if (currentView === "chats") renderChatList();
            }
        });
    }

    // 4. 개발자 패널 데이터 갱신 (키 동기화)
    if (roomId === currentRoomId && data.current_status) {
        const status = data.current_status;
        if (status.medium_term_plan)
            document.getElementById("m-plan").innerText =
                status.medium_term_plan;
        if (status.short_term_plan)
            document.getElementById("s-plan").innerText =
                status.short_term_plan;

        if (status.short_term_logs) {
            const sfLog = document.getElementById("short-feeling-log");
            sfLog.innerText = status.short_term_logs.join("\n");
            sfLog.scrollTop = sfLog.scrollHeight;
        }
        if (status.medium_term_logs) {
            const mrLog = document.getElementById("medium-record-log");
            mrLog.innerText = status.medium_term_logs.join("\n");
            mrLog.scrollTop = mrLog.scrollHeight;
        }
        if (status.fact_warehouse) {
            const fwLog = document.getElementById("fact-warehouse-log");
            fwLog.innerText = status.fact_warehouse
                .map((fact) => `• ${fact}`)
                .join("\n");
            fwLog.scrollTop = fwLog.scrollHeight;
        }
        Object.assign(f, status);
        syncUI(f);
    }
}

function showInAppNoti(friend, text, roomId) {
    const popup = document.getElementById("noti-popup");
    const colors = ["#FF9500", "#FF2D55", "#AF52DE", "#5AC8FA", "#34C759"];
    const avatar = document.getElementById("noti-avatar");
    avatar.style.background = colors[friend.name.charCodeAt(0) % 5];
    avatar.innerText = friend.name[0];
    document.getElementById("noti-name").innerText = friend.name;
    document.getElementById("noti-text").innerText = text;
    notiTargetRoomId = roomId;

    popup.classList.add("show");
    setTimeout(() => popup.classList.remove("show"), 4000);
}

function handleNotiClick() {
    if (notiTargetRoomId) {
        joinRoom(notiTargetRoomId);
        document.getElementById("noti-popup").classList.remove("show");
    }
}

async function joinRoom(roomId) {
    // 성능 최적화: 현재 대화 중인 방만 소켓 유지
    currentRoomId = roomId;
    unreadCounts[roomId] = 0;

    // [추가] 프로필 뷰가 열려있다면 닫기
    closeProfile();

    switchMobileView("chat");

    const f = friendsData.find((item) => item.room_id === roomId);
    document.getElementById("active-friend-name").innerText =
        `${f.name} (${f.mbti})`;

    const statusEl = document.getElementById("active-friend-status");
    statusEl.innerText = f.status === "online" ? "온라인" : "오프라인";
    statusEl.className = `status ${f.status || "offline"}`;

    const chatArea = document.getElementById("chat-area");
    chatArea.innerHTML = "";
    if (f.history) {
        f.history.forEach((h) =>
            appendMsg(h.role === "user" ? "user" : "ai", h.content, h.ts),
        );
    }
    syncUI(f);

    // 대화방 입장 시 소켓 연결
    initSocket(roomId);
}

function syncUI(f) {
    const pi = document.getElementById("persona-info");
    if (pi)
        pi.innerHTML = `이름: <b>${f.name}</b> / 나이: <b>${f.age}세</b> / 성별: <b>${f.gender}</b><br>MBTI: <b>${f.mbti}</b>`;
    const keys = [
        "p_seriousness",
        "p_friendliness",
        "p_rationality",
        "p_slang",
        "v_likeability",
        "v_erotic",
        "v_v_mood",
        "v_relationship",
    ];
    keys.forEach((k) => {
        const el = document.getElementById(k);
        if (el && document.activeElement !== el) el.value = f[k] ?? 50;
    });
}

function send() {
    const input = document.getElementById("msg-input");
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
    const row = document.createElement("div");
    row.className = `msg-row ${type}`;
    row.innerHTML = `<div class="bubble ${type}">${text}</div><div class="msg-meta">${ts}</div>`;
    area.appendChild(row);
    area.scrollTop = area.scrollHeight;
}

async function commitParams() {
    if (!currentRoomId) return;
    const keys = [
        "p_seriousness",
        "p_friendliness",
        "p_rationality",
        "p_slang",
        "v_likeability",
        "v_erotic",
        "v_v_mood",
        "v_relationship",
    ];
    const params = {};
    keys.forEach((k) => {
        params[k] = parseInt(document.getElementById(k).value) || 0;
    });
    // [수정] 인증 헤더 추가
    const res = await fetch(`/update-params/${currentRoomId}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify(params),
    });
    if (res.ok) alert("이브의 상태가 변경되었습니다.");
}

async function deleteFriend(event, roomId) {
    if (event && event.stopPropagation) event.stopPropagation();
    if (confirm("이 대화방을 삭제하시겠습니까? (친구 목록에는 유지됩니다)")) {
        // [수정] 백엔드 API 연동 및 인증 헤더 추가
        const res = await fetch(`/delete-friend/${roomId}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (res.ok) {
            const f = friendsData.find((item) => item.room_id === roomId);
            if (f) f.history = [];
            renderChatList();
        }
    }
}

function setEngineStatus(isActive) {
    const dot = document.getElementById("engine-dot");
    const text = document.getElementById("engine-text");
    if (dot) dot.className = isActive ? "status-dot active" : "status-dot";
    if (text) text.innerText = isActive ? "Active" : "Offline";
}

async function addFriend() {
    const btn = document.getElementById("add-friend-btn");
    btn.innerText = "+ 친구 찾는 중...";
    btn.disabled = true;
    // [수정] 인증 헤더 추가
    await fetch("/add-friend", {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    btn.innerText = "+ 새로운 친구 찾기";
    btn.disabled = false;
    loadFriends();
}

async function resetDB() {
    if (confirm("DB를 초기화합니까?")) {
        // [수정] 인증 헤더 추가
        await fetch("/reset-db", {
            method: "POST",
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        location.reload();
    }
}

async function shutdownServer() {
    if (confirm("서버를 종료합니까?"))
        await fetch("/shutdown", {
            method: "POST",
            headers: { Authorization: `Bearer ${accessToken}` },
        });
}

// [신규] 관리자: 유저 목록 로드
async function loadAdminUsers() {
    const res = await fetch("/admin/users", {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (res.ok) {
        const users = await res.json();
        const list = document.getElementById("admin-user-list");
        list.innerHTML = users
            .map(
                (u) => `
            <div class="user-info-card" onclick="viewUserDetail(${u.id}, '${u.username}')">
                <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                    <span style="font-weight:900;">${u.username} ${u.is_admin ? "(Admin)" : ""}</span>
                    <span class="token-badge">${u.total_tokens.toLocaleString()} tokens</span>
                </div>
                <div style="font-size:10px; color:var(--text-sub);">
                    가입: ${u.created_at ? new Date(u.created_at).toLocaleDateString() : "N/A"} / 활동: ${u.last_active ? new Date(u.last_active).toLocaleString() : "N/A"}
                </div>
            </div>
        `,
            )
            .join("");
    }
}

// [신규] 관리자: 유저 상세 정보 보기
async function viewUserDetail(userId, username) {
    const res = await fetch(`/admin/user/${userId}/detail`, {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (res.ok) {
        const rooms = await res.json();
        document.getElementById("admin-user-view").style.display = "none";
        const detailView = document.getElementById("admin-detail-view");
        detailView.style.display = "block";
        document.getElementById("admin-detail-title").innerText =
            `${username}'s EVEs`;

        const content = document.getElementById("admin-detail-content");
        content.innerHTML = rooms
            .map(
                (r) => `
            <div class="user-info-card" style="cursor:default;">
                <div style="font-weight:900; margin-bottom:5px;">${r.persona_name} (Msg: ${r.history_count})</div>
                <div style="font-size:11px; margin-bottom:8px; color:var(--blue);">${r.last_summary}</div>
                <div style="font-size:10px; color:var(--text-sub); border-top:1px solid var(--border); padding-top:5px;">
                    <b>Facts:</b> ${r.fact_warehouse.slice(0, 3).join(", ") || "No facts stored."}
                </div>
            </div>
        `,
            )
            .join("");

        document.getElementById("admin-delete-user-btn").onclick = () =>
            adminDeleteUser(userId, username);
    }
}

// [신규] 관리자: 유저 삭제
async function adminDeleteUser(userId, username) {
    if (
        confirm(
            `진짜로 '${username}' 계정을 삭제하시겠습니까? 연동된 모든 데이터가 사라집니다.`,
        )
    ) {
        const res = await fetch(`/admin/user/${userId}`, {
            method: "DELETE",
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (res.ok) {
            alert("삭제되었습니다.");
            switchAdminTab("users");
        }
    }
}

// [신규] 사용자 권한 및 정보에 따른 UI 업데이트
function updateUIByAuth() {
    const userDisplay = document.getElementById("user-display");
    const devBtn = document.getElementById("dev-btn");
    const navDev = document.getElementById("nav-dev");

    if (accessToken) {
        // 아이디 표시
        if (userDisplay) userDisplay.innerText = currentUsername || "";

        // 관리자 권한 확인 및 버튼 노출 제어
        if (isAdmin) {
            if (devBtn) devBtn.style.display = "flex";
            if (navDev) navDev.style.display = "flex";
        } else {
            if (devBtn) devBtn.style.display = "none";
            if (navDev) navDev.style.display = "none";
        }
    }
}

// 초기 실행
checkAuth();
switchMobileView("list");
