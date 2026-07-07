// Global App State
let currentUser = null;
let currentRole = null; // 'candidate', 'hr', or 'admin'
let systemState = null;
let pollInterval = null;

// DOM Elements
const loginScreen = document.getElementById('login-screen');
const portalScreen = document.getElementById('portal-screen');
const loginForm = document.getElementById('login-form');
const usernameInput = document.getElementById('username');
const passwordInput = document.getElementById('password');
const roleBadge = document.getElementById('role-badge');
const userDisplayName = document.getElementById('user-display-name');
const logoutBtn = document.getElementById('logout-btn');

// Sidebars & layout columns
const announcementsSidebar = document.getElementById('announcements-sidebar');

// Menu Groups
const candidateMenu = document.getElementById('candidate-menu');
const hrMenu = document.getElementById('hr-menu');
const adminMenu = document.getElementById('admin-menu');

// Profile Header Sidebar
const sidebarName = document.getElementById('sidebar-name');
const sidebarEmail = document.getElementById('sidebar-email');

// Handle Login Form Submit
loginForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const username = usernameInput.value.trim();
    const password = passwordInput.value.trim();

    authenticate(username, password);
});

async function authenticate(username, password) {
    // 1. Fetch current database state to check user credentials
    try {
        const res = await fetch('/api/state');
        const state = await res.json();
        systemState = state;
        
        let authenticated = false;
        let role = null;
        let userData = null;

        // Check defaults first
        if (username === 'admin' && password === 'password') {
            authenticated = true;
            role = 'admin';
            userData = { name: 'PES Chairperson', email: 'chairperson@pes.edu' };
        } else if (username === 'hr' && password === 'password') {
            authenticated = true;
            role = 'hr';
            userData = { name: 'HR Desk Officer', email: 'hr.onboarding@pes.edu' };
        } else if (state.teachers && state.teachers[username]) {
            const teacher = state.teachers[username];
            if (teacher.password === password) {
                authenticated = true;
                role = 'candidate';
                userData = teacher;
            }
        }

        if (!authenticated) {
            alert('Invalid credentials. Please refer to login hint below the card.');
            return;
        }

        // Setup session
        currentUser = username;
        currentRole = role;

        // Visual routing transformations
        loginScreen.classList.add('hidden');
        portalScreen.classList.remove('hidden');

        // Render menus based on role
        candidateMenu.classList.add('hidden');
        hrMenu.classList.add('hidden');
        adminMenu.classList.add('hidden');

        if (role === 'candidate') {
            candidateMenu.classList.remove('hidden');
            roleBadge.innerText = 'Candidate / Teacher';
            roleBadge.className = 'badge badge-info';
            announcementsSidebar.classList.remove('hidden');
            // Trigger default tab
            switchTab('candidate-profile');
        } else if (role === 'hr') {
            hrMenu.classList.remove('hidden');
            roleBadge.innerText = 'HR Department';
            roleBadge.className = 'badge badge-success';
            announcementsSidebar.classList.add('hidden');
            switchTab('hr-teachers-list');
        } else if (role === 'admin') {
            adminMenu.classList.remove('hidden');
            roleBadge.innerText = 'Chairperson / Admin';
            roleBadge.className = 'badge badge-danger';
            announcementsSidebar.classList.add('hidden');
            switchTab('admin-seating-allotment');
        }

        // Set sidebar user details
        sidebarName.innerText = userData.name;
        sidebarEmail.innerText = userData.email;
        userDisplayName.innerText = userData.name;

        // Render data values
        updateDashboardView();

        // Start real-time polling
        clearInterval(pollInterval);
        pollInterval = setInterval(syncStateData, 3000);

    } catch (e) {
        console.error(e);
        alert('Server communication error. Make sure the uvicorn server is running.');
    }
}

// Sign Out
logoutBtn.addEventListener('click', () => {
    currentUser = null;
    currentRole = null;
    portalScreen.classList.add('hidden');
    loginScreen.classList.remove('hidden');
    usernameInput.value = '';
    passwordInput.value = '';
    clearInterval(pollInterval);
});

// Periodic Synchronization
async function syncStateData() {
    try {
        const res = await fetch('/api/state');
        systemState = await res.json();
        updateDashboardView();
    } catch (e) {
        console.error('Sync failed', e);
    }
}

// Tab switcher handler
document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', (e) => {
        const targetTab = e.target.getAttribute('data-tab');
        switchTab(targetTab);
    });
});

function switchTab(tabId) {
    // Deactivate current tabs
    document.querySelectorAll('.nav-tab').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.add('hidden'));

    // Activate selected
    const activeTabButton = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
    if (activeTabButton) activeTabButton.classList.add('active');

    const targetPane = document.getElementById(`tab-${tabId}`);
    if (targetPane) targetPane.classList.remove('hidden');
}

// Update DOM elements using loaded state
function updateDashboardView() {
    if (!systemState) return;

    // 1. Render Announcements right panel
    const annListView = document.getElementById('announcements-list-view');
    annListView.innerHTML = '';
    const sortedAnn = [...systemState.announcements].reverse();
    sortedAnn.forEach(ann => {
        const annDiv = document.createElement('div');
        annDiv.className = 'ann-item';
        annDiv.innerHTML = `
            <h4>${ann.title}</h4>
            <p>${ann.content}</p>
            <div class="ann-meta">
                <span>By: ${ann.sender}</span>
                <span>Date: ${ann.date}</span>
            </div>
        `;
        annListView.appendChild(annDiv);
    });

    // 2. Load candidate specific panels if Candidate is active
    if (currentRole === 'candidate' && systemState.teachers[currentUser]) {
        const teacher = systemState.teachers[currentUser];
        
        // Profile
        document.getElementById('prof-name').innerText = teacher.name;
        document.getElementById('prof-email').innerText = teacher.email;
        document.getElementById('prof-dept').innerText = teacher.department;
        document.getElementById('prof-desig').innerText = teacher.designation;
        document.getElementById('prof-leaves').innerText = teacher.leave_balance;

        // Seating Info
        const seatVal = document.getElementById('seating-allocated-val');
        seatVal.innerText = teacher.seating_info || 'Not Allotted';

        // Calendar Schedule
        const scheduleBody = document.getElementById('calendar-schedule-body');
        scheduleBody.innerHTML = '';
        if (teacher.schedule && teacher.schedule.length > 0) {
            teacher.schedule.forEach(s => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${s.day}</strong></td>
                    <td>${s.time}</td>
                    <td>${s.subject}</td>
                    <td><span class="badge badge-info">${s.class}</span></td>
                `;
                scheduleBody.appendChild(tr);
            });
        } else {
            scheduleBody.innerHTML = '<tr><td colspan="4" class="text-muted text-center">No classes scheduled</td></tr>';
        }

        // Attendance Record
        const absentCount = document.getElementById('attendance-absent-count');
        absentCount.innerText = teacher.attendance ? teacher.attendance.length : 0;
        
        const attendanceBody = document.getElementById('attendance-record-body');
        attendanceBody.innerHTML = '';
        if (teacher.attendance && teacher.attendance.length > 0) {
            teacher.attendance.forEach(att => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${att.date}</td>
                    <td><span class="badge badge-danger">${att.status}</span></td>
                    <td>${att.reason}</td>
                `;
                attendanceBody.appendChild(tr);
            });
        } else {
            attendanceBody.innerHTML = '<tr><td colspan="3" class="text-muted text-center">Perfect attendance record</td></tr>';
        }

        // Submitted Docs
        const submittedDocsList = document.getElementById('submitted-doc-list');
        submittedDocsList.innerHTML = '';
        if (teacher.documents && teacher.documents.length > 0) {
            teacher.documents.forEach(doc => {
                const li = document.createElement('li');
                li.innerHTML = `📄 ${doc} <span class="badge badge-success" style="float:right">Uploaded</span>`;
                submittedDocsList.appendChild(li);
            });
        } else {
            submittedDocsList.innerHTML = '<li class="text-muted">No documents submitted yet</li>';
        }

    }

    // 3. Render HR Views
    if (currentRole === 'hr') {
        const teachersListContainer = document.getElementById('hr-teachers-list-view');
        teachersListContainer.innerHTML = '';
        
        Object.keys(systemState.teachers).forEach(uname => {
            const t = systemState.teachers[uname];
            const div = document.createElement('div');
            div.className = 'teacher-card-item';
            div.innerHTML = `
                <div class="teacher-card-info">
                    <h4>${t.name} (@${t.username})</h4>
                    <p>${t.designation} - ${t.department}</p>
                    <p style="font-size:0.8rem; color:var(--text-secondary); margin-bottom: 2px;">Email: ${t.email || 'N/A'}</p>
                    <p style="font-size:0.75rem; color:var(--text-muted)">Seating: ${t.seating_info}</p>
                </div>
                <button class="btn btn-secondary btn-sm edit-profile-btn" data-username="${t.username}">Edit Profile</button>
            `;
            // Trigger Edit profile click
            div.querySelector('.edit-profile-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                openEditDrawer(t.username);
            });
            teachersListContainer.appendChild(div);
        });
    }

    // 4. Render Admin / Chairperson Views
    if (currentRole === 'admin') {
        // Populate seating allotment select box
        const allotTeacherSelect = document.getElementById('allot-teacher-select');
        const selectedVal = allotTeacherSelect.value;
        allotTeacherSelect.innerHTML = '';
        
        Object.keys(systemState.teachers).forEach(uname => {
            const t = systemState.teachers[uname];
            const opt = document.createElement('option');
            opt.value = t.username;
            opt.innerText = `${t.name} (${t.username})`;
            allotTeacherSelect.appendChild(opt);
        });
        
        if (selectedVal && Object.keys(systemState.teachers).includes(selectedVal)) {
            allotTeacherSelect.value = selectedVal;
        }

        // Populate admin teachers list overview table
        const adminTeachersTableBody = document.getElementById('admin-teachers-table-body');
        adminTeachersTableBody.innerHTML = '';
        Object.keys(systemState.teachers).forEach(uname => {
            const t = systemState.teachers[uname];

            let seatingHTML = 'Not Allotted';
            if (t.seating_info && t.seating_info.includes(',')) {
                const parts = t.seating_info.split(',');
                seatingHTML = `<div style="font-weight:600; color:#fff">${parts[0].trim()}</div><div style="font-size:0.75rem; color:var(--text-secondary)">${parts[1].trim()}</div>`;
            } else if (t.seating_info) {
                seatingHTML = `<div style="font-weight:600; color:#fff">${t.seating_info}</div>`;
            }

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${t.name}</strong></td>
                <td>${t.department}</td>
                <td>${t.email}</td>
                <td>${seatingHTML}</td>
                <td>${t.documents ? t.documents.length : 0} file(s)</td>
            `;
            adminTeachersTableBody.appendChild(tr);
        });
    }
}

// HR Add Teacher Form
const hrAddTeacherForm = document.getElementById('hr-add-teacher-form');
hrAddTeacherForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
        name: document.getElementById('add-name').value,
        email: document.getElementById('add-email').value,
        department: document.getElementById('add-dept').value,
        designation: document.getElementById('add-desig').value
    };

    try {
        const res = await fetch('/api/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'add_teacher', payload })
        });
        
        if (res.ok) {
            alert('Teacher profile successfully created!');
            hrAddTeacherForm.reset();
            switchTab('hr-teachers-list');
            syncStateData();
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (e) {
        alert('Server communication error.');
    }
});

// Open edit drawer helper
const editDrawer = document.getElementById('hr-edit-drawer');
const closeDrawerBtn = document.getElementById('close-drawer-btn');
closeDrawerBtn.addEventListener('click', () => editDrawer.classList.add('hidden'));

function openEditDrawer(username) {
    const t = systemState.teachers[username];
    if (!t) return;
    
    document.getElementById('edit-username').value = username;
    document.getElementById('edit-name').value = t.name;
    document.getElementById('edit-email').value = t.email;
    document.getElementById('edit-dept').value = t.department;
    document.getElementById('edit-desig').value = t.designation;
    
    editDrawer.classList.remove('hidden');
}

// Edit Form Submit
const hrEditForm = document.getElementById('hr-edit-form');
hrEditForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('edit-username').value;
    const payload = {
        username: username,
        name: document.getElementById('edit-name').value,
        email: document.getElementById('edit-email').value,
        department: document.getElementById('edit-dept').value,
        designation: document.getElementById('edit-desig').value
    };

    try {
        const res = await fetch('/api/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'update_teacher', payload })
        });
        
        if (res.ok) {
            alert('Teacher profile updated!');
            editDrawer.classList.add('hidden');
            syncStateData();
        } else {
            const err = await res.json();
            alert(`Error: ${err.detail}`);
        }
    } catch (e) {
        alert('Server communication error.');
    }
});

// Delete Teacher Profile Action
const deleteTeacherBtn = document.getElementById('delete-teacher-btn');
deleteTeacherBtn.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const username = document.getElementById('edit-username').value;
    if (!username) return;
    
    if (confirm(`Are you sure you want to permanently delete the profile for @${username}?`)) {
        try {
            const res = await fetch('/api/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    action: 'delete_teacher',
                    payload: { username }
                })
            });
            
            if (res.ok) {
                alert('Teacher profile successfully deleted.');
                editDrawer.classList.add('hidden');
                syncStateData();
            } else {
                const err = await res.json();
                alert(`Error: ${err.detail}`);
            }
        } catch (e) {
            alert('Server communication error.');
        }
    }
});

// Candidate document upload action
const submitDocBtn = document.getElementById('submit-doc-btn');
const documentInput = document.getElementById('document-input');
submitDocBtn.addEventListener('click', async () => {
    const docName = documentInput.value.trim();
    if (!docName) return alert('Please enter a document filename.');

    try {
        const res = await fetch('/api/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: 'upload_document',
                payload: { username: currentUser, document_name: docName }
            })
        });
        if (res.ok) {
            documentInput.value = '';
            syncStateData();
        } else {
            alert('Upload failed');
        }
    } catch (e) {
        alert('Server communication error.');
    }
});

// Admin Allotment Form
const adminAllotmentForm = document.getElementById('admin-allotment-form');
adminAllotmentForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
        username: document.getElementById('allot-teacher-select').value,
        seating_info: document.getElementById('allot-seating-input').value.trim()
    };

    try {
        const res = await fetch('/api/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'allot_seat', payload })
        });
        if (res.ok) {
            alert('Seating coordinates successfully allotted!');
            document.getElementById('allot-seating-input').value = '';
            syncStateData();
        } else {
            alert('Seat allotment failed.');
        }
    } catch (e) {
        alert('Server communication error.');
    }
});

// Admin Announcement Form
const adminAnnouncementForm = document.getElementById('admin-announcement-form');
adminAnnouncementForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
        title: document.getElementById('ann-title').value.trim(),
        content: document.getElementById('ann-content').value.trim(),
        sender: 'Admin'
    };

    try {
        const res = await fetch('/api/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'add_announcement', payload })
        });
        if (res.ok) {
            alert('Announcement successfully broadcast!');
            adminAnnouncementForm.reset();
            syncStateData();
        } else {
            alert('Announcement broadcast failed.');
        }
    } catch (e) {
        alert('Server communication error.');
    }
});

// Full-screen Chatbot Interactivity
const fullscreenChatSend = document.getElementById('fullscreen-chat-send');
const fullscreenChatInput = document.getElementById('fullscreen-chat-input');
const fullscreenChatBody = document.getElementById('fullscreen-chat-body');

fullscreenChatSend.addEventListener('click', sendFullscreenChatMessage);
fullscreenChatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendFullscreenChatMessage();
});

async function sendFullscreenChatMessage() {
    const text = fullscreenChatInput.value.trim();
    if (!text) return;

    appendFullscreenChatBubble('user', text);
    fullscreenChatInput.value = '';
    fullscreenChatBody.scrollTop = fullscreenChatBody.scrollHeight;

    // Show thinking indicator bubble
    const thinkingBubble = appendFullscreenChatBubble('bot', 'Thinking...');
    thinkingBubble.id = 'thinking-bubble';

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        });
        
        // Remove thinking bubble
        const tb = document.getElementById('thinking-bubble');
        if (tb) tb.remove();

        const data = await res.json();
        appendFullscreenChatBubble('bot', data.response);
    } catch (e) {
        const tb = document.getElementById('thinking-bubble');
        if (tb) tb.remove();
        appendFullscreenChatBubble('bot', 'Error communicating with Pinecone RAG search agent.');
    }
    fullscreenChatBody.scrollTop = fullscreenChatBody.scrollHeight;
}

function formatMarkdown(text) {
    if (!text) return '';
    // Escape HTML first to prevent XSS
    let escaped = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");

    // Convert double asterisks bold: **text** -> <strong>text</strong>
    escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Convert single asterisks bold: *text* -> <strong>text</strong>
    escaped = escaped.replace(/\*(.*?)\*/g, '<strong>$1</strong>');
    // Convert newlines to line breaks
    escaped = escaped.replace(/\n/g, '<br>');
    
    return escaped;
}

function appendFullscreenChatBubble(sender, text) {
    const bubble = document.createElement('div');
    bubble.className = `chat-message ${sender}`;
    bubble.innerHTML = formatMarkdown(text);
    fullscreenChatBody.appendChild(bubble);
    return bubble;
}
