// Second Brain Client-Side Logic

// Global Application State
const state = {
    currentRoute: 'dashboard',
    dashboardFilter: 'weekly',
    projects: [],
    activeProjectId: null,
    activeProjectDetails: null,
    activeTaskId: null,
    memories: [],
    memoryTypes: [],
    memoryTags: [],
    chatHistory: [
        {
            role: 'assistant',
            content: "Hello! I am your Second Brain. Ask me questions about your notes, tasks, or log new activities and memories naturally."
        }
    ],
    chatGroupId: null,
    isChatStreaming: false
};

// API Base URL (Relative to host)
const API_BASE = "";

// Initialize App
document.addEventListener("DOMContentLoaded", () => {
    // Setup Router
    window.addEventListener("hashchange", handleRouting);
    handleRouting();

    // Setup Event Listeners
    setupEventListeners();

    // Initialize Lucide Icons
    lucide.createIcons();
});

// Navigation & Router
function handleRouting() {
    const hash = window.location.hash || '#/dashboard';
    const route = hash.replace('#/', '');
    state.currentRoute = route;

    // Update active nav link
    document.querySelectorAll(".sidebar-nav .nav-item").forEach(item => {
        item.classList.remove("active");
    });
    const activeNav = document.getElementById(`nav-${route}`);
    if (activeNav) activeNav.classList.add("active");

    // Switch panels
    document.querySelectorAll(".content-panel").forEach(panel => {
        panel.classList.remove("active");
    });
    const activePanel = document.getElementById(`panel-${route}`);
    if (activePanel) activePanel.classList.add("active");

    // Update header filter visibility
    const filterGroup = document.getElementById("dashboard-filters");
    if (route === 'dashboard') {
        filterGroup.style.display = 'flex';
    } else {
        filterGroup.style.display = 'none';
    }

    // Set page title
    const pageTitle = document.getElementById("page-title");
    pageTitle.textContent = route.charAt(0).toUpperCase() + route.slice(1);

    // Load data for specific route
    loadRouteData(route);
}

function loadRouteData(route) {
    if (route === 'dashboard') {
        fetchDashboardSummary();
    } else if (route === 'projects') {
        fetchProjects();
    } else if (route === 'memories') {
        fetchMemories();
        fetchTagsAndTypes();
    } else if (route === 'chat') {
        renderChatHistory();
    }
}

// Event Listeners Setup
function setupEventListeners() {
    // Dashboard Filter Buttons
    document.querySelectorAll(".filter-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            state.dashboardFilter = e.target.dataset.filter;
            fetchDashboardSummary();
        });
    });

    // Quick Add Modal type toggle shows/hides expense fields
    const memTypeSelect = document.getElementById("mem-type");
    if (memTypeSelect) {
        memTypeSelect.addEventListener("change", (e) => {
            const expFields = document.getElementById("expense-fields");
            if (e.target.value === 'expense') {
                expFields.style.display = 'block';
            } else {
                expFields.style.display = 'none';
            }
        });
    }

    // Memory Search & Filters
    const searchInput = document.getElementById("memory-search-input");
    if (searchInput) {
        let debounceTimer;
        searchInput.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                fetchMemories();
            }, 300);
        });
    }

    const typeFilter = document.getElementById("memory-type-filter");
    if (typeFilter) {
        typeFilter.addEventListener("change", fetchMemories);
    }

    const tagFilter = document.getElementById("memory-tag-filter");
    if (tagFilter) {
        tagFilter.addEventListener("change", fetchMemories);
    }

    // Global Key Shortcuts (Cmd+K / Ctrl+K for command palette)
    document.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            openCommandPalette();
        }
        if (e.key === 'Escape') {
            closeAllModals();
        }
    });
}

// Close all active modals/drawers
function closeAllModals() {
    closeQuickAddModal();
    closeCommandPalette();
    closeCreateProjectModal();
    closeTaskDetailsDrawer();
}

// Markdown formatting helper for AI Summary
function formatMarkdown(text) {
    if (!text) return "";
    // Convert bold text **word** to <strong>word</strong>
    let html = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    const lines = html.split('\n');
    let inList = false;
    let resultLines = [];
    
    lines.forEach(line => {
        const trimmed = line.trim();
        if (trimmed.startsWith('### ')) {
            if (inList) {
                resultLines.push('</ul>');
                inList = false;
            }
            resultLines.push(`<h3 class="insight-heading">${trimmed.slice(4)}</h3>`);
        } else if (trimmed.startsWith('## ')) {
            if (inList) {
                resultLines.push('</ul>');
                inList = false;
            }
            resultLines.push(`<h3 class="insight-heading">${trimmed.slice(3)}</h3>`);
        } else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
            if (!inList) {
                resultLines.push('<ul>');
                inList = true;
            }
            resultLines.push(`<li>${trimmed.slice(2)}</li>`);
        } else {
            if (inList) {
                resultLines.push('</ul>');
                inList = false;
            }
            if (trimmed) {
                resultLines.push(`<p>${trimmed}</p>`);
            }
        }
    });
    
    if (inList) {
        resultLines.push('</ul>');
    }
    
    return resultLines.join('\n');
}

// --- Dashboard Functions ---

async function fetchDashboardSummary(forceRefresh = false) {
    const summaryText = document.getElementById("ai-summary-text");
    if (summaryText) {
        summaryText.innerHTML = '<span class="cmd-feedback-spinner" style="display:inline-block; width:12px; height:12px; vertical-align:middle; margin-right:6px;"></span> AI Agent is compiling your insights...';
    }

    const refreshBtn = document.querySelector(".btn-refresh-ai");
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.style.opacity = "0.5";
        refreshBtn.style.pointerEvents = "none";
    }

    try {
        const res = await fetch(`${API_BASE}/api/dashboard/summary?filter=${state.dashboardFilter}&refresh=${forceRefresh}`);
        const data = await res.json();
        
        // Render AI Insights
        if (summaryText) {
            summaryText.innerHTML = formatMarkdown(data.ai_summary || "No insights compiled.");
        }

        // Render Last Updated Timestamp
        const updatedText = document.getElementById("ai-summary-updated");
        if (updatedText && data.updated_at) {
            const d = new Date(data.updated_at);
            const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            const dateStr = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
            updatedText.textContent = `Last updated: ${dateStr} at ${timeStr}`;
        }
        
        // Render KPIs
        document.getElementById("kpi-total-tasks").textContent = 
            data.tasks.open + data.tasks["in-progress"] + data.tasks.done + data.tasks.cancelled;
        document.getElementById("kpi-task-breakdown").textContent = 
            `${data.tasks.open} to do / ${data.tasks["in-progress"]} in progress`;
        document.getElementById("kpi-completed-tasks").textContent = data.tasks.done;
        
        const totalTasks = data.tasks.open + data.tasks["in-progress"] + data.tasks.done;
        const completionRate = totalTasks > 0 ? Math.round((data.tasks.done / totalTasks) * 100) : 0;
        document.getElementById("kpi-completion-rate").textContent = `${completionRate}% completion rate`;
        
        document.getElementById("kpi-total-expenses").textContent = data.expenses.total.toFixed(2);
        document.getElementById("kpi-expenses-currency").textContent = data.expenses.currency || "INR";
        
        document.getElementById("kpi-overdue-tasks").textContent = data.tasks.overdue;

        // Render Expense Categories
        renderExpenseCategories(data.expenses.by_category, data.expenses.total, data.expenses.currency);

        // Render Activity Timeline
        renderActivityTimeline(data.recent_activities);

        // Render Projects Progress
        renderProjectsProgress(data.projects);

        // Render Expense Chart (SVG)
        renderExpenseChart(data.expenses.history, data.expenses.currency);

    } catch (err) {
        console.error("Error loading dashboard data:", err);
        if (summaryText) {
            summaryText.textContent = "Failed to load AI Insights.";
        }
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.style.opacity = "1";
            refreshBtn.style.pointerEvents = "auto";
        }
    }
}

function renderExpenseCategories(byCategory, total, currency) {
    const container = document.getElementById("expense-categories-list");
    if (!byCategory || Object.keys(byCategory).length === 0) {
        container.innerHTML = '<div class="empty-state">No expense breakdown available</div>';
        return;
    }

    container.innerHTML = "";
    // Sort categories by amount desc
    const sorted = Object.entries(byCategory).sort((a, b) => b[1] - a[1]);

    sorted.forEach(([cat, val]) => {
        const pct = total > 0 ? (val / total) * 100 : 0;
        const item = document.createElement("div");
        item.className = "category-progress-item";
        item.innerHTML = `
            <div class="category-progress-info">
                <span class="category-name">${cat}</span>
                <span class="category-value">${val.toFixed(2)} ${currency}</span>
            </div>
            <div class="progress-bar-bg">
                <div class="progress-bar-fill" style="width: ${pct}%"></div>
            </div>
        `;
        container.appendChild(item);
    });
}

function renderActivityTimeline(activities) {
    const container = document.getElementById("recent-activity-timeline");
    if (!activities || activities.length === 0) {
        container.innerHTML = '<div class="timeline-empty">No recent activity</div>';
        return;
    }

    container.innerHTML = "";
    activities.forEach(act => {
        const item = document.createElement("div");
        item.className = "timeline-item";
        
        // Tags markup
        const tagsMarkup = act.tags && act.tags.length > 0 
            ? `<div class="timeline-tags">${act.tags.map(t => `<span class="tag-badge">#${t}</span>`).join('')}</div>`
            : '';

        let detailMarkup = "";
        if (act.type === 'expense') {
            detailMarkup = `
                <div class="expense-detail">
                    <span class="expense-detail-amt">${act.amount} ${act.currency || 'INR'}</span>
                    <span class="expense-detail-cat">${act.category || 'uncategorized'}</span>
                </div>
            `;
        }

        const dateFormatted = act.occurred_at ? act.occurred_at.split('T')[0] : '';

        item.innerHTML = `
            <div class="timeline-dot ${act.type}"></div>
            <div class="timeline-content">
                <div class="timeline-header">
                    <span class="timeline-title">${act.type.toUpperCase()}</span>
                    <span class="timeline-time">${dateFormatted}</span>
                </div>
                <div class="timeline-desc">${act.raw_text}</div>
                ${detailMarkup}
                ${tagsMarkup}
            </div>
        `;
        container.appendChild(item);
    });
}

function renderProjectsProgress(projects) {
    const container = document.getElementById("project-mini-list");
    if (!projects || projects.length === 0) {
        container.innerHTML = '<div class="empty-state">No projects loaded</div>';
        return;
    }

    container.innerHTML = "";
    projects.forEach(p => {
        const item = document.createElement("div");
        item.className = "project-mini-item";
        item.onclick = () => {
            window.location.hash = `#/projects`;
            state.activeProjectId = p.id;
        };

        item.innerHTML = `
            <div class="project-mini-header">
                <span class="project-mini-title">${p.project}</span>
                <span class="project-mini-org">${p.org}</span>
            </div>
            <div class="project-mini-progress">
                <span class="project-mini-text">${p.open_tasks} open task${p.open_tasks === 1 ? '' : 's'}</span>
            </div>
        `;
        container.appendChild(item);
    });
}

function renderExpenseChart(history, currency) {
    const container = document.getElementById("expense-trend-chart");
    if (!history || history.length === 0) {
        container.innerHTML = '<div class="chart-empty">No expense data in this period</div>';
        return;
    }

    const width = 450;
    const height = 180;
    const paddingLeft = 40;
    const paddingRight = 20;
    const paddingTop = 20;
    const paddingBottom = 30;

    const chartWidth = width - paddingLeft - paddingRight;
    const chartHeight = height - paddingTop - paddingBottom;

    // Get max total
    const maxVal = Math.max(...history.map(d => d.total), 100);
    
    // Generate Coordinates
    const points = history.map((d, index) => {
        const x = paddingLeft + (index / Math.max(history.length - 1, 1)) * chartWidth;
        const y = height - paddingBottom - (d.total / maxVal) * chartHeight;
        return { x, y, val: d.total, date: d.occurred_at.split('T')[0] };
    });

    let pathD = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length; i++) {
        pathD += ` L ${points[i].x} ${points[i].y}`;
    }

    // Gradient area path
    const areaD = `${pathD} L ${points[points.length - 1].x} ${height - paddingBottom} L ${points[0].x} ${height - paddingBottom} Z`;

    // Horizontal grid lines
    const gridLines = [];
    const gridDivs = 3;
    for (let i = 0; i <= gridDivs; i++) {
        const val = (i / gridDivs) * maxVal;
        const y = height - paddingBottom - (i / gridDivs) * chartHeight;
        gridLines.push(`
            <line x1="${paddingLeft}" y1="${y}" x2="${width - paddingRight}" y2="${y}" class="chart-grid-line" />
            <text x="${paddingLeft - 8}" y="${y + 4}" class="chart-label" text-anchor="end">${Math.round(val)}</text>
        `);
    }

    // Date markers
    const dateMarkers = [];
    const skip = Math.ceil(history.length / 5);
    history.forEach((d, idx) => {
        if (idx % skip === 0 || idx === history.length - 1) {
            const x = points[idx].x;
            const labelDate = d.occurred_at.split('-').slice(1).join('/'); // MM/DD
            dateMarkers.push(`
                <text x="${x}" y="${height - paddingBottom + 16}" class="chart-label" text-anchor="middle">${labelDate}</text>
            `);
        }
    });

    // Dots & Tooltips
    const dots = points.map(p => `
        <circle cx="${p.x}" cy="${p.y}" r="4" class="chart-dot">
            <title>${p.date}: ${p.val.toFixed(2)} ${currency}</title>
        </circle>
    `).join('');

    container.innerHTML = `
        <svg viewBox="0 0 ${width} ${height}" class="svg-chart-container">
            <defs>
                <linearGradient id="chart-gradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="var(--primary)" stop-opacity="0.3"/>
                    <stop offset="100%" stop-color="var(--primary)" stop-opacity="0.0"/>
                </linearGradient>
            </defs>
            ${gridLines.join('')}
            ${dateMarkers.join('')}
            <path d="${areaD}" class="chart-area" />
            <path d="${pathD}" class="chart-line" />
            ${dots}
        </svg>
    `;
}

// --- Projects & Kanban board Functions ---

async function fetchProjects() {
    try {
        const res = await fetch(`${API_BASE}/api/projects`);
        const projects = await res.json();
        state.projects = projects;

        renderProjectsMenu();

        // Auto select active project or first project
        if (state.activeProjectId) {
            selectProject(state.activeProjectId);
        } else if (projects.length > 0) {
            selectProject(projects[0].id);
        } else {
            renderEmptyBoardState();
        }
    } catch (err) {
        console.error("Error fetching projects:", err);
    }
}

function renderProjectsMenu() {
    const container = document.getElementById("projects-menu-list");
    container.innerHTML = "";
    
    state.projects.forEach(p => {
        const btn = document.createElement("button");
        btn.className = `project-list-btn ${state.activeProjectId === p.id ? 'active' : ''}`;
        btn.id = `proj-btn-${p.id}`;
        btn.onclick = () => selectProject(p.id);
        btn.innerHTML = `
            <span class="project-btn-title">${p.project}</span>
            <span class="project-btn-org">${p.org}</span>
        `;
        container.appendChild(btn);
    });
}

async function selectProject(projectId) {
    state.activeProjectId = projectId;
    
    // Highlight sidebar active item
    document.querySelectorAll(".project-list-btn").forEach(btn => btn.classList.remove("active"));
    const activeBtn = document.getElementById(`proj-btn-${projectId}`);
    if (activeBtn) activeBtn.classList.add("active");

    try {
        const res = await fetch(`${API_BASE}/api/projects/${projectId}`);
        const details = await res.json();
        state.activeProjectDetails = details;

        // Show board view
        document.getElementById("active-project-name").textContent = details.project.project;
        document.getElementById("active-project-org").textContent = details.project.org;
        document.getElementById("btn-board-add-task").style.display = "inline-flex";
        document.getElementById("kanban-board").style.display = "grid";
        document.getElementById("board-empty-state").style.display = "none";

        renderKanbanBoard(details.tasks);

    } catch (err) {
        console.error("Error loading project details:", err);
    }
}

function renderEmptyBoardState() {
    document.getElementById("active-project-name").textContent = "Select a Project";
    document.getElementById("active-project-org").textContent = "";
    document.getElementById("btn-board-add-task").style.display = "none";
    document.getElementById("kanban-board").style.display = "none";
    document.getElementById("board-empty-state").style.display = "flex";
}

function renderKanbanBoard(tasks) {
    const columns = {
        "open": document.getElementById("column-todo"),
        "in-progress": document.getElementById("column-inprogress"),
        "done": document.getElementById("column-done"),
        "cancelled": document.getElementById("column-cancelled")
    };

    const counts = {
        "open": document.getElementById("count-todo"),
        "in-progress": document.getElementById("count-inprogress"),
        "done": document.getElementById("count-done"),
        "cancelled": document.getElementById("count-cancelled")
    };

    // Reset columns
    Object.values(columns).forEach(col => col.innerHTML = "");
    const colCounts = { "open": 0, "in-progress": 0, "done": 0, "cancelled": 0 };

    tasks.forEach(t => {
        const status = t.status || 'open';
        if (!columns[status]) return;

        colCounts[status]++;
        const card = document.createElement("div");
        card.className = "kanban-card";
        card.draggable = true;
        card.ondragstart = (e) => handleDragStart(e, t.id);
        card.onclick = () => openTaskDetailsDrawer(t.id);

        const priorityBadge = t.priority 
            ? `<span class="priority-badge ${t.priority.toLowerCase()}">${t.priority}</span>`
            : '';

        const progressMarkup = t.progress && t.progress.total > 0
            ? `<span><i data-lucide="list-todo"></i> ${t.progress.done}/${t.progress.total}</span>`
            : '';

        const attachmentCountMarkup = t.attachments && t.attachments.length > 0
            ? `<span><i data-lucide="paperclip"></i> ${t.attachments.length}</span>`
            : '';

        const dateFormatted = t.due_at ? t.due_at.split('T')[0] : '';
        const dueMarkup = dateFormatted
            ? `<span><i data-lucide="calendar"></i> ${dateFormatted}</span>`
            : '';

        const subtextMarkup = t.description 
            ? `<div class="card-desc">${t.description}</div>`
            : '';

        card.innerHTML = `
            <div class="card-title">${t.title}</div>
            ${subtextMarkup}
            <div class="card-footer">
                <div class="card-meta">
                    ${dueMarkup}
                    ${progressMarkup}
                    ${attachmentCountMarkup}
                </div>
                ${priorityBadge}
            </div>
        `;
        columns[status].appendChild(card);
    });

    // Update column counts
    Object.entries(counts).forEach(([status, element]) => {
        element.textContent = colCounts[status];
    });

    // Re-initialize Lucide Icons for task cards
    lucide.createIcons();
}

// Drag & Drop Handlers
function handleDragStart(e, taskId) {
    e.dataTransfer.setData("text/plain", taskId);
    e.dataTransfer.effectAllowed = "move";
}

function allowDrop(e) {
    e.preventDefault();
}

async function handleDrop(e, status) {
    e.preventDefault();
    const taskId = e.dataTransfer.getData("text/plain");
    
    try {
        const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status })
        });
        
        if (res.ok) {
            // Reload project
            if (state.activeProjectId) {
                selectProject(state.activeProjectId);
            }
        }
    } catch (err) {
        console.error("Error dropping task card:", err);
    }
}

// --- Task details Drawer Functions ---

async function openTaskDetailsDrawer(taskId) {
    state.activeTaskId = taskId;
    const drawerOverlay = document.getElementById("task-details-drawer");
    
    // Find task details from current project tasks list
    const task = state.activeProjectDetails.tasks.find(t => t.id === taskId);
    if (!task) return;

    // Load drawer elements
    document.getElementById("drawer-task-project").textContent = task.project;
    document.getElementById("drawer-task-org").textContent = task.org;
    document.getElementById("drawer-task-status-select").value = task.status;
    document.getElementById("drawer-task-priority-select").value = task.priority || "";
    document.getElementById("drawer-task-title").textContent = task.title;
    document.getElementById("drawer-task-desc").textContent = task.description || "";
    document.getElementById("drawer-task-due").value = task.due_at ? task.due_at.split('T')[0] : "";
    
    if (task.recur_freq) {
        document.getElementById("drawer-recur-row").style.display = "flex";
        document.getElementById("drawer-task-recurrence").textContent = `Every ${task.recur_interval} ${task.recur_freq}(s)`;
    } else {
        document.getElementById("drawer-recur-row").style.display = "none";
    }

    // Toggle complete button
    const completeBtn = document.getElementById("drawer-complete-btn");
    if (task.status === 'done') {
        completeBtn.style.display = "none";
    } else {
        completeBtn.style.display = "block";
    }

    // Load subtasks & attachments
    renderDrawerSubtasks(task.id);
    renderDrawerAttachments(task.attachments);

    // Open drawer
    drawerOverlay.classList.add("active");
    lucide.createIcons();
}

function closeTaskDetailsDrawer(e) {
    if (!e || e.target.id === 'task-details-drawer' || e.target.closest('.btn-close')) {
        document.getElementById("task-details-drawer").classList.remove("active");
        state.activeTaskId = null;
        // Refresh active board
        if (state.activeProjectId) {
            selectProject(state.activeProjectId);
        }
    }
}

async function updateActiveTaskField(fields) {
    if (!state.activeTaskId) return;
    try {
        await fetch(`${API_BASE}/api/tasks/${state.activeTaskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(fields)
        });
    } catch (err) {
        console.error("Error updating task field:", err);
    }
}

function handleDrawerTitleBlur() {
    const title = document.getElementById("drawer-task-title").textContent.trim();
    if (title) updateActiveTaskField({ title });
}

function handleDrawerDescBlur() {
    const description = document.getElementById("drawer-task-desc").textContent.trim();
    updateActiveTaskField({ description });
}

function handleDrawerStatusChange() {
    const status = document.getElementById("drawer-task-status-select").value;
    updateActiveTaskField({ status });
    
    // Toggle complete button
    const completeBtn = document.getElementById("drawer-complete-btn");
    if (status === 'done') {
        completeBtn.style.display = "none";
    } else {
        completeBtn.style.display = "block";
    }
}

function handleDrawerPriorityChange() {
    const priority = document.getElementById("drawer-task-priority-select").value;
    updateActiveTaskField({ priority });
}

function handleDrawerDueChange() {
    const due_at = document.getElementById("drawer-task-due").value;
    updateActiveTaskField({ due_at });
}

async function handleDrawerCompleteTask() {
    if (!state.activeTaskId) return;
    try {
        const res = await fetch(`${API_BASE}/api/tasks/${state.activeTaskId}/complete`, {
            method: 'POST'
        });
        const data = await res.json();
        if (data.ok) {
            // Close drawer & reload
            document.getElementById("task-details-drawer").classList.remove("active");
            if (state.activeProjectId) {
                selectProject(state.activeProjectId);
            }
        }
    } catch (err) {
        console.error("Error completing task:", err);
    }
}

// Drawer Subtasks Management
async function renderDrawerSubtasks(taskId) {
    const listContainer = document.getElementById("drawer-subtasks-list");
    listContainer.innerHTML = `<div class="empty-state">Loading subtasks...</div>`;

    try {
        // Fetch project details fresh to get latest subtasks
        const res = await fetch(`${API_BASE}/api/projects/${state.activeProjectId}`);
        const details = await res.json();
        state.activeProjectDetails = details;
        
        const task = details.tasks.find(t => t.id === taskId);
        // Find subtasks: subtasks are tasks that have parent_id = taskId
        const subtasks = details.tasks.filter(t => t.parent_id === taskId);

        // Update progress badge
        const progressCount = document.getElementById("drawer-subtask-progress");
        const completedCount = subtasks.filter(s => s.status === 'done').length;
        progressCount.textContent = `${completedCount}/${subtasks.length}`;

        if (subtasks.length === 0) {
            listContainer.innerHTML = '<div class="empty-state">No checklist items yet</div>';
            return;
        }

        listContainer.innerHTML = "";
        subtasks.forEach(sub => {
            const item = document.createElement("div");
            item.className = `subtask-item ${sub.status === 'done' ? 'done' : ''}`;
            
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.className = "subtask-checkbox";
            checkbox.checked = sub.status === 'done';
            checkbox.onclick = () => toggleSubtaskStatus(sub.id, checkbox.checked);

            const span = document.createElement("span");
            span.textContent = sub.title;

            item.appendChild(checkbox);
            item.appendChild(span);
            listContainer.appendChild(item);
        });

    } catch (err) {
        console.error("Error rendering subtasks:", err);
    }
}

async function toggleSubtaskStatus(subtaskId, isChecked) {
    const status = isChecked ? 'done' : 'open';
    try {
        const res = await fetch(`${API_BASE}/api/tasks/${subtaskId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status })
        });
        if (res.ok) {
            renderDrawerSubtasks(state.activeTaskId);
        }
    } catch (err) {
        console.error("Error toggling subtask:", err);
    }
}

async function handleAddSubtask(e) {
    e.preventDefault();
    const input = document.getElementById("new-subtask-title");
    const title = input.value.trim();
    if (!title || !state.activeTaskId) return;

    const parentTask = state.activeProjectDetails.tasks.find(t => t.id === state.activeTaskId);

    try {
        const res = await fetch(`${API_BASE}/api/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title,
                parent_task_id: state.activeTaskId,
                project: parentTask.project,
                org: parentTask.org
            })
        });
        if (res.ok) {
            input.value = "";
            renderDrawerSubtasks(state.activeTaskId);
        }
    } catch (err) {
        console.error("Error adding subtask:", err);
    }
}

// Drawer Attachments Management
function renderDrawerAttachments(attachments) {
    const listContainer = document.getElementById("drawer-attachments-list");
    if (!attachments || attachments.length === 0) {
        listContainer.innerHTML = '<div class="empty-state">No attachments yet</div>';
        return;
    }

    listContainer.innerHTML = "";
    attachments.forEach(att => {
        const card = document.createElement("div");
        card.className = "attachment-card";
        
        let icon = "link";
        if (att.type === 'image') icon = "image";
        if (att.type === 'file') icon = "file-text";

        card.innerHTML = `
            <a href="${att.url}" target="_blank" class="attachment-info">
                <i data-lucide="${icon}"></i>
                <span>${att.description || att.url}</span>
            </a>
            <span class="memory-date">${att.created_at ? att.created_at.split('T')[0] : ''}</span>
        `;
        listContainer.appendChild(card);
    });
    lucide.createIcons();
}

async function handleAddAttachment(e) {
    e.preventDefault();
    const urlInput = document.getElementById("new-attachment-url");
    const descInput = document.getElementById("new-attachment-desc");
    const typeSelect = document.getElementById("new-attachment-type");

    const url = urlInput.value.trim();
    const description = descInput.value.trim();
    const type = typeSelect.value;

    if (!url || !state.activeTaskId) return;

    try {
        const res = await fetch(`${API_BASE}/api/tasks/${state.activeTaskId}/attachments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, description, type })
        });
        const data = await res.json();
        if (data.ok) {
            urlInput.value = "";
            descInput.value = "";
            
            // Reload project & redraw drawer attachments
            const projectRes = await fetch(`${API_BASE}/api/projects/${state.activeProjectId}`);
            const details = await projectRes.json();
            state.activeProjectDetails = details;
            const updatedTask = details.tasks.find(t => t.id === state.activeTaskId);
            renderDrawerAttachments(updatedTask.attachments);
        }
    } catch (err) {
        console.error("Error adding attachment:", err);
    }
}

// --- Memories / Explorer Functions ---

async function fetchMemories() {
    const query = document.getElementById("memory-search-input").value.trim();
    const type = document.getElementById("memory-type-filter").value;
    const tag = document.getElementById("memory-tag-filter").value;

    let url = `${API_BASE}/api/memories?limit=100`;
    if (query) url += `&query=${encodeURIComponent(query)}`;
    if (type) url += `&type=${encodeURIComponent(type)}`;
    if (tag) url += `&tag=${encodeURIComponent(tag)}`;

    try {
        const res = await fetch(url);
        const memories = await res.json();
        state.memories = memories;
        renderMemoriesList(memories);
    } catch (err) {
        console.error("Error fetching memories:", err);
    }
}

async function fetchTagsAndTypes() {
    try {
        // Types
        const typesRes = await fetch(`${API_BASE}/api/types`);
        const types = await typesRes.json();
        state.memoryTypes = types;
        
        const typeFilter = document.getElementById("memory-type-filter");
        const selectedType = typeFilter.value;
        typeFilter.innerHTML = '<option value="">All Types</option>';
        types.forEach(t => {
            const opt = document.createElement("option");
            opt.value = t;
            opt.textContent = t.toUpperCase();
            if (t === selectedType) opt.selected = true;
            typeFilter.appendChild(opt);
        });

        // Tags
        const tagsRes = await fetch(`${API_BASE}/api/tags`);
        const tags = await tagsRes.json();
        state.memoryTags = tags;

        const tagFilter = document.getElementById("memory-tag-filter");
        const selectedTag = tagFilter.value;
        tagFilter.innerHTML = '<option value="">All Tags</option>';
        tags.forEach(t => {
            const opt = document.createElement("option");
            opt.value = t;
            opt.textContent = `#${t}`;
            if (t === selectedTag) opt.selected = true;
            tagFilter.appendChild(opt);
        });

    } catch (err) {
        console.error("Error loading tags/types filters:", err);
    }
}

function renderMemoriesList(memories) {
    const container = document.getElementById("memories-grid");
    if (!memories || memories.length === 0) {
        container.innerHTML = '<div class="board-empty" style="grid-column: 1/-1;"><p>No matching memories found</p></div>';
        return;
    }

    container.innerHTML = "";
    memories.forEach(mem => {
        const card = document.createElement("div");
        card.className = "memory-card glass";

        const typeClass = mem.type;
        const dateFormatted = mem.occurred_at ? mem.occurred_at.split('T')[0] : '';
        const tagsMarkup = mem.tags && mem.tags.length > 0
            ? `<div class="timeline-tags">${mem.tags.map(t => `<span class="tag-badge">#${t}</span>`).join('')}</div>`
            : '';

        let detailMarkup = "";
        if (mem.type === 'expense') {
            detailMarkup = `
                <div class="expense-detail">
                    <span class="expense-detail-amt">${mem.amount} ${mem.currency || 'INR'}</span>
                    <span class="expense-detail-cat">${mem.category || 'uncategorized'}</span>
                </div>
            `;
        }

        // Format links
        let rawTextFormatted = mem.raw_text;
        if (mem.type === 'link') {
            const urlMatch = rawTextFormatted.match(/(https?:\/\/[^\s]+)/g);
            if (urlMatch) {
                urlMatch.forEach(url => {
                    rawTextFormatted = rawTextFormatted.replace(url, `<a href="${url}" target="_blank">${url}</a>`);
                });
            }
        }

        card.innerHTML = `
            <div class="memory-card-header">
                <span class="memory-type-badge ${typeClass}">${mem.type}</span>
                <span class="memory-date">${dateFormatted}</span>
            </div>
            <div class="memory-body">${rawTextFormatted}</div>
            ${detailMarkup}
            ${tagsMarkup}
        `;
        container.appendChild(card);
    });
}

// --- AI Chat Functions ---

function renderChatHistory() {
    const container = document.getElementById("chat-messages-container");
    container.innerHTML = "";

    state.chatHistory.forEach(msg => {
        const bubble = document.createElement("div");
        bubble.className = `message ${msg.role}`;
        
        let icon = "brain";
        if (msg.role === 'user') icon = "user";

        // Convert newlines to paragraphs/breaks
        const contentFormatted = msg.content
            .split('\n\n')
            .map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`)
            .join('');

        bubble.innerHTML = `
            <div class="message-avatar">
                <i data-lucide="${icon}"></i>
            </div>
            <div class="message-content">
                ${contentFormatted}
            </div>
        `;
        container.appendChild(bubble);
    });
    
    // Add quick prompts to the initial assistant message if history has only 1 message
    if (state.chatHistory.length === 1) {
        const promptBlock = document.createElement("div");
        promptBlock.className = "quick-prompts";
        promptBlock.innerHTML = `
            <button class="quick-prompt-btn" onclick="sendQuickPrompt('spent 350 INR on uber today')">"spent 350 INR on uber today"</button>
            <button class="quick-prompt-btn" onclick="sendQuickPrompt('add a high priority task prepare presentation in project Work')">"add a task prepare presentation..."</button>
            <button class="quick-prompt-btn" onclick="sendQuickPrompt('what did I do this week?')">"what did I do this week?"</button>
            <button class="quick-prompt-btn" onclick="sendQuickPrompt('show open tasks due this week')">"show open tasks due this week"</button>
        `;
        container.querySelector(".message-content").appendChild(promptBlock);
    }

    lucide.createIcons();
    scrollChatToBottom();
}

function scrollChatToBottom() {
    const container = document.getElementById("chat-messages-container");
    container.scrollTop = container.scrollHeight;
}

function sendQuickPrompt(text) {
    document.getElementById("chat-input").value = text;
    document.getElementById("chat-form").requestSubmit();
}

async function handleChatSubmit(e) {
    e.preventDefault();
    if (state.isChatStreaming) return;

    const input = document.getElementById("chat-input");
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    state.isChatStreaming = true;

    // Append user message
    state.chatHistory.push({ role: 'user', content: message });
    renderChatHistory();

    // Setup assistant response placeholder
    const container = document.getElementById("chat-messages-container");
    const bubble = document.createElement("div");
    bubble.className = "message assistant";
    bubble.innerHTML = `
        <div class="message-avatar"><i data-lucide="brain"></i></div>
        <div class="message-content" id="streaming-bubble-content">
            <span class="cmd-feedback-spinner" style="display: inline-block; width:12px; height:12px;"></span> Thinking...
        </div>
    `;
    container.appendChild(bubble);
    lucide.createIcons();
    scrollChatToBottom();

    const statusText = document.getElementById("chat-status-text");
    statusText.textContent = "AI is typing...";

    try {
        const res = await fetch(`${API_BASE}/api/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                history: state.chatHistory.slice(0, -1), // Send history before this turn
                group_id: state.chatGroupId
            })
        });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let responseText = "";

        const streamingContent = document.getElementById("streaming-bubble-content");

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep partial line in buffer

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    try {
                        const jsonStr = line.slice(6).trim();
                        if (!jsonStr) continue;

                        const data = JSON.parse(jsonStr);
                        if (data.type === 'delta') {
                            responseText += data.text;
                            // Format paragraphs
                            const formatted = responseText
                                .split('\n\n')
                                .map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`)
                                .join('');
                            streamingContent.innerHTML = formatted;
                            scrollChatToBottom();
                        } else if (data.type === 'done') {
                            state.chatHistory = data.history;
                            state.chatGroupId = data.group_id;
                        } else if (data.type === 'error') {
                            streamingContent.innerHTML = `<span class="text-danger">Error: ${data.message}</span>`;
                        }
                    } catch (err) {
                        console.error("SSE parse error:", err, "Line was:", line);
                    }
                }
            }
        }
    } catch (err) {
        console.error("Chat streaming error:", err);
        const streamingContent = document.getElementById("streaming-bubble-content");
        streamingContent.innerHTML = `<span class="text-danger">Failed to connect to AI server.</span>`;
    } finally {
        state.isChatStreaming = false;
        statusText.textContent = "Ready";
        renderChatHistory(); // Redraw final formatted response
    }
}

// --- Quick Add Modals and Palettes ---

function openQuickAddModal() {
    document.getElementById("quick-add-modal").classList.add("active");
    // Default tab is memory, reset forms
    switchQuickAddTab('memory');
    document.getElementById("form-quick-add-memory").reset();
    document.getElementById("form-quick-add-task").reset();
    document.getElementById("expense-fields").style.display = 'none';
    
    // Set default date to today
    document.getElementById("mem-date").valueAsDate = new Date();
    document.getElementById("task-due").valueAsDate = new Date();
}

function closeQuickAddModal(e) {
    if (!e || e.target.id === 'quick-add-modal' || e.target.closest('.btn-close')) {
        document.getElementById("quick-add-modal").classList.remove("active");
    }
}

function switchQuickAddTab(tabName) {
    document.querySelectorAll(".modal-tabs .tab-btn").forEach(btn => {
        btn.classList.remove("active");
    });
    document.querySelectorAll(".modal-content .tab-content").forEach(content => {
        content.classList.remove("active");
    });

    const activeBtn = Array.from(document.querySelectorAll(".modal-tabs .tab-btn")).find(btn => btn.textContent.toLowerCase().includes(tabName));
    if (activeBtn) activeBtn.classList.add("active");

    const contentId = tabName === 'memory' ? 'form-quick-add-memory' : 'form-quick-add-task';
    document.getElementById(contentId).classList.add("active");
}

async function handleQuickAddMemory(e) {
    e.preventDefault();
    const raw_text = document.getElementById("mem-raw-text").value.trim();
    const type = document.getElementById("mem-type").value;
    const occurred_at = document.getElementById("mem-date").value;
    const tags = document.getElementById("mem-tags").value.split(',').map(t => t.trim()).filter(t => t);

    const payload = { type, raw_text, occurred_at, tags };

    if (type === 'expense') {
        payload.amount = parseFloat(document.getElementById("mem-amount").value) || 0;
        payload.currency = document.getElementById("mem-currency").value.trim() || 'INR';
        payload.category = document.getElementById("mem-category").value.trim() || 'uncategorized';
    }

    try {
        const res = await fetch(`${API_BASE}/api/memories`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            closeQuickAddModal();
            loadRouteData(state.currentRoute);
        }
    } catch (err) {
        console.error("Error saving quick memory:", err);
    }
}

async function handleQuickAddTask(e) {
    e.preventDefault();
    const title = document.getElementById("task-title").value.trim();
    const description = document.getElementById("task-desc").value.trim();
    const project = document.getElementById("task-project").value.trim();
    const org = document.getElementById("task-org").value.trim();
    const priority = document.getElementById("task-priority").value;
    const due_at = document.getElementById("task-due").value;
    const recur_freq = document.getElementById("task-recur-freq").value;
    const recur_interval = parseInt(document.getElementById("task-recur-interval").value) || 1;

    const payload = {
        title, description, project, org, priority, due_at,
        recur_freq: recur_freq || null,
        recur_interval
    };

    try {
        const res = await fetch(`${API_BASE}/api/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            closeQuickAddModal();
            loadRouteData(state.currentRoute);
        }
    } catch (err) {
        console.error("Error creating quick task:", err);
    }
}

// Kanban View Project quick add task
function openAddTaskModal() {
    openQuickAddModal();
    switchQuickAddTab('task');
    // Pre-fill project and org fields if in active project board
    if (state.activeProjectDetails) {
        document.getElementById("task-project").value = state.activeProjectDetails.project.project;
        document.getElementById("task-org").value = state.activeProjectDetails.project.org;
    }
}

// Create Project Dialog
function openCreateProjectModal() {
    document.getElementById("create-project-modal").classList.add("active");
    document.getElementById("form-create-project").reset();
}

function closeCreateProjectModal(e) {
    if (!e || e.target.id === 'create-project-modal' || e.target.closest('.btn-close')) {
        document.getElementById("create-project-modal").classList.remove("active");
    }
}

async function handleCreateProject(e) {
    e.preventDefault();
    const project = document.getElementById("new-project-name").value.trim();
    const org = document.getElementById("new-project-org").value.trim();

    try {
        const res = await fetch(`${API_BASE}/api/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project, org })
        });
        const data = await res.json();
        if (res.ok) {
            closeCreateProjectModal();
            state.activeProjectId = data.id;
            fetchProjects();
        }
    } catch (err) {
        console.error("Error creating project:", err);
    }
}

// Command Palette (Cmd+K Modal)
function openCommandPalette() {
    document.getElementById("cmd-palette-modal").classList.add("active");
    const input = document.getElementById("cmd-search-input");
    input.value = "";
    input.focus();
    
    // Reset help list in UI
    resetCommandPaletteHelp();
}

function closeCommandPalette(e) {
    if (!e || e.target.id === 'cmd-palette-modal') {
        document.getElementById("cmd-palette-modal").classList.remove("active");
    }
}

function resetCommandPaletteHelp() {
    const list = document.getElementById("cmd-results-list");
    list.innerHTML = `
        <div class="cmd-help">
            <div class="cmd-help-title">Natural Language Quick Add examples (Press Enter to execute):</div>
            <ul class="cmd-help-list">
                <li><span>"paid 649 INR for netflix today #subscription"</span> - Logs an expense</li>
                <li><span>"remind me to write the report by Friday at 4 PM"</span> - Creates a task with due date</li>
                <li><span>"project Work: build database architecture"</span> - Adds task in project</li>
                <li><span>"had a coffee meeting with Sarah today"</span> - Logs a note memory</li>
            </ul>
            <div class="cmd-help-footer">
                <span>Press <kbd>ESC</kbd> to close. AI agent router parses commands automatically.</span>
            </div>
        </div>
    `;
}

async function handleCommandPaletteKeyDown(e) {
    if (e.key === 'Enter') {
        const input = document.getElementById("cmd-search-input");
        const query = input.value.trim();
        if (!query) return;

        // Show spinner / loader
        const list = document.getElementById("cmd-results-list");
        list.innerHTML = `
            <div class="cmd-feedback">
                <span class="cmd-feedback-spinner"></span>
                <span class="cmd-feedback-message">Processing with AI Agent...</span>
            </div>
        `;

        try {
            // Send command as a chat message turn to save preference/memory/task
            const res = await fetch(`${API_BASE}/api/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: query,
                    history: state.chatHistory,
                    group_id: state.chatGroupId
                })
            });

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let text = "";
            let data = null;

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                const lines = decoder.decode(value).split('\n');
                for (const line of lines) {
                    if (line.startsWith("data: ")) {
                        const jsonStr = line.slice(6).trim();
                        if (jsonStr) {
                            const parsed = JSON.parse(jsonStr);
                            if (parsed.type === 'delta') {
                                text += parsed.text;
                            } else if (parsed.type === 'done') {
                                data = parsed;
                            }
                        }
                    }
                }
            }

            if (data) {
                // Update history
                state.chatHistory = data.history;
                state.chatGroupId = data.group_id;

                // Show confirmation
                list.innerHTML = `
                    <div class="cmd-feedback">
                        <i data-lucide="check-circle" style="color:var(--success); width:32px; height:32px;"></i>
                        <span class="cmd-feedback-message" style="color:var(--text-primary); font-weight:600;">Agent Processed successfully!</span>
                        <span class="cmd-feedback-message">${text}</span>
                        <button class="btn btn-secondary" onclick="closeAllModals()" style="margin-top:8px;">Dismiss</button>
                    </div>
                `;
                lucide.createIcons();

                // Reload active panels
                loadRouteData(state.currentRoute);
            } else {
                throw new Error("Failed to process command.");
            }

        } catch (err) {
            console.error("Command palette execution error:", err);
            list.innerHTML = `
                <div class="cmd-feedback">
                    <i data-lucide="alert-octagon" style="color:var(--danger); width:32px; height:32px;"></i>
                    <span class="cmd-feedback-message" style="color:var(--danger);">Failed to execute natural language statement.</span>
                    <button class="btn btn-secondary" onclick="resetCommandPaletteHelp()" style="margin-top:8px;">Try Again</button>
                </div>
            `;
            lucide.createIcons();
        }
    }
}
