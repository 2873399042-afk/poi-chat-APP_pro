// ========== 全局变量 ==========
let map = null;
let typeChart = echarts.init(document.getElementById('typeChart'));
let markers = [];
let currentPoiCoords = [];
let sessionId = 'session_' + Math.random().toString(36).substr(2, 9);
let currentMode = 'chat';
let isWaitingForResponse = false;

// ========== 页面加载完成初始化 ==========
window.onload = function() {
    initMap();
    bindEvents();
    switchMode('chat');
    // 初始快捷回复
    renderDefaultQuickReplies();
};

// ========== 初始化带默认底图的地图 ==========
function initMap() {
    map = new AMap.Map('map', {
        zoom: 13,
        center: [118.89, 32.12],
        viewMode: '3D',
        pitch: 10,
        layers: [
            new AMap.createDefaultLayer({
                style: 'amap://styles/light',
                zIndex: 1
            })
        ],
        resizeEnable: true
    });
}

// ========== 绑定所有事件 ==========
function bindEvents() {
    // 模式切换
    document.getElementById('chatModeBtn').addEventListener('click', () => switchMode('chat'));
    document.getElementById('searchModeBtn').addEventListener('click', () => switchMode('search'));

    // 对话模式
    document.getElementById('chatSendBtn').addEventListener('click', sendChatMessage);
    document.getElementById('chatInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendChatMessage();
    });

    // 快速搜索模式
    document.getElementById('searchBtn').addEventListener('click', doSearch);
    document.getElementById('queryInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') doSearch();
    });

    window.addEventListener('resize', () => typeChart.resize());
}

// ========== 模式切换 ==========
function switchMode(mode) {
    currentMode = mode;
    const chatBtn = document.getElementById('chatModeBtn');
    const searchBtn = document.getElementById('searchModeBtn');
    const chatContainer = document.getElementById('chatContainer');
    const searchContainer = document.getElementById('searchContainer');

    if (mode === 'chat') {
        chatBtn.classList.add('active');
        searchBtn.classList.remove('active');
        chatContainer.classList.remove('hidden');
        searchContainer.classList.add('hidden');
    } else {
        searchBtn.classList.add('active');
        chatBtn.classList.remove('active');
        chatContainer.classList.add('hidden');
        searchContainer.classList.remove('hidden');
    }
}

// ========== 渲染默认快捷回复 ==========
function renderDefaultQuickReplies() {
    const replies = [
        {"label": "🍽 餐饮类", "payload": "餐饮类"},
        {"label": "📚 教育类", "payload": "教育类"},
        {"label": "🚇 交通类", "payload": "交通类"},
        {"label": "🛒 购物类", "payload": "购物类"},
        {"label": "🏨 住宿类", "payload": "住宿类"},
        {"label": "🏥 医疗类", "payload": "医疗类"},
        {"label": "🎬 娱乐类", "payload": "娱乐类"},
    ];
    renderQuickReplies(replies);
}

// ========== 渲染快捷回复按钮 ==========
function renderQuickReplies(quickReplies) {
    const container = document.getElementById('quickReplies');
    container.innerHTML = '';
    if (!quickReplies || quickReplies.length === 0) return;

    quickReplies.forEach(reply => {
        const btn = document.createElement('button');
        btn.className = 'quick-reply-btn';
        btn.textContent = reply.label;
        btn.addEventListener('click', () => {
            if (isWaitingForResponse) return;
            document.getElementById('chatInput').value = reply.payload;
            sendChatMessage();
        });
        container.appendChild(btn);
    });
}

// ========== 核心：发送对话消息 ==========
async function sendChatMessage() {
    if (isWaitingForResponse) return;

    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message) return;

    // 添加用户消息气泡
    appendChatBubble('user', message);
    input.value = '';
    isWaitingForResponse = true;

    // 禁用输入
    document.getElementById('chatSendBtn').disabled = true;
    document.getElementById('chatInput').disabled = true;

    // 显示输入中动画
    showTypingIndicator();

    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, session_id: sessionId })
        });
        const data = await response.json();

        // 移除输入中动画
        hideTypingIndicator();

        if (data.error) {
            appendChatBubble('assistant', '抱歉，出错了：' + data.error);
            return;
        }

        // 添加助手消息气泡
        appendChatBubble('assistant', data.message, data.type);

        // 根据响应类型处理
        if (data.type === 'follow_up') {
            renderQuickReplies(data.quick_replies);
        } else if (data.type === 'results') {
            // 更新所有可视化
            currentPoiCoords = data.results.map(poi => [parseFloat(poi.lng), parseFloat(poi.lat)]);
            renderInsight(data.insight);
            renderPoiList(data.results);
            renderMapMarkers(data.results);
            renderTypeChart(data.results);
            document.getElementById('totalCount').textContent = data.total;
            renderQuickReplies(data.quick_replies);
        }

    } catch (error) {
        console.error('对话失败', error);
        hideTypingIndicator();
        appendChatBubble('assistant', '抱歉，连接失败，请检查后端服务是否正常运行。');
    } finally {
        isWaitingForResponse = false;
        document.getElementById('chatSendBtn').disabled = false;
        document.getElementById('chatInput').disabled = false;
        document.getElementById('chatInput').focus();
    }
}

// ========== 添加消息气泡 ==========
function appendChatBubble(role, message, type) {
    const container = document.getElementById('chatMessages');
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble ' + role;
    if (type === 'results') {
        bubble.className += ' results';
    }
    bubble.innerHTML = message.replace(/\n/g, '<br>');
    container.appendChild(bubble);

    // 滚动到底部
    container.scrollTop = container.scrollHeight;
}

// ========== 输入中动画 ==========
function showTypingIndicator() {
    const container = document.getElementById('chatMessages');
    const indicator = document.createElement('div');
    indicator.className = 'typing-indicator';
    indicator.id = 'typingIndicator';
    indicator.innerHTML = '<span></span><span></span><span></span>';
    container.appendChild(indicator);
    container.scrollTop = container.scrollHeight;
}

function hideTypingIndicator() {
    const indicator = document.getElementById('typingIndicator');
    if (indicator) {
        indicator.remove();
    }
}

// ========== 快速搜索：doSearch（保持原有逻辑） ==========
async function doSearch() {
    const query = document.getElementById('queryInput').value.trim();
    if (!query) return;

    clearMapMarkers();
    document.getElementById('poiList').innerHTML = '';
    document.getElementById('insightText').textContent = '正在分析中...';
    document.getElementById('totalCount').textContent = '0';
    currentPoiCoords = [];

    try {
        const response = await fetch('/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, session_id: sessionId })
        });
        const data = await response.json();
        if (data.error) {
            alert(data.error);
            return;
        }

        currentPoiCoords = data.results.map(poi => [parseFloat(poi.lng), parseFloat(poi.lat)]);
        renderInsight(data.insight);
        renderPoiList(data.results);
        renderMapMarkers(data.results);
        renderTypeChart(data.results);
        document.getElementById('totalCount').textContent = data.total;

    } catch (error) {
        console.error('查询失败', error);
        alert('查询失败，请检查后端服务是否正常运行');
    }
}

// ========== 渲染空间洞察 ==========
function renderInsight(insight) {
    document.getElementById('insightText').textContent = insight;
}

// ========== 渲染POI列表 ==========
function renderPoiList(pois) {
    const list = document.getElementById('poiList');
    list.innerHTML = '';
    pois.forEach(poi => {
        const item = document.createElement('li');
        item.className = 'poi-item';
        item.innerHTML = `
            <div class="name">${poi.name}</div>
            <div class="address">${poi.address}</div>
            <div class="coords"> 经纬度: ${poi.lng}, ${poi.lat}</div>
            ${poi.has_sub_poi ? '<span class="tag">✅ 满足附属条件</span>' : ''}
        `;
        item.addEventListener('click', () => {
            map.setZoomAndCenter(15, [parseFloat(poi.lng), parseFloat(poi.lat)]);
        });
        list.appendChild(item);
    });
}

// ========== 渲染地图POI标记 ==========
function renderMapMarkers(pois) {
    clearMapMarkers();
    if (pois.length === 0) return;
    const firstPoi = pois[0];
    map.setZoomAndCenter(13, [parseFloat(firstPoi.lng), parseFloat(firstPoi.lat)]);

    pois.forEach(poi => {
        const lng = parseFloat(poi.lng);
        const lat = parseFloat(poi.lat);
        if (!lng || !lat) return;

        const marker = new AMap.Marker({
            position: [lng, lat],
            title: poi.name,
            icon: 'https://webapi.amap.com/theme/v1.3/markers/n/mark_b.png'
        });
        marker.setMap(map);
        markers.push(marker);

        marker.on('click', () => {
            new AMap.InfoWindow({
                content: `<h4 style="margin-bottom:5px;">${poi.name}</h4><p style="margin:0;color:#666;">${poi.address}</p>`,
                offset: new AMap.Pixel(0, -30)
            }).open(map, [lng, lat]);
        });
    });
}

// ========== 渲染POI类型分布图表 ==========
function renderTypeChart(pois) {
    const typeCount = {};
    pois.forEach(poi => {
        const firstType = poi.type.split(';')[0] || '未知类型';
        typeCount[firstType] = (typeCount[firstType] || 0) + 1;
    });

    const chartData = Object.entries(typeCount).map(([name, value]) => ({ name, value }));

    const option = {
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: {
            type: 'category',
            data: chartData.map(d => d.name),
            axisLabel: { rotate: 30 }
        },
        yAxis: { type: 'value' },
        series: [{
            type: 'bar',
            data: chartData.map(d => d.value),
            itemStyle: { color: '#409eff' },
            barWidth: '40%'
        }]
    };

    typeChart.setOption(option, true);
}

// ========== 清空地图标记 ==========
function clearMapMarkers() {
    markers.forEach(marker => marker.setMap(null));
    markers = [];
}
