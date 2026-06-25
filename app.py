// ============================================================
// TV REMOTE CONTROL SUPPORT
// ============================================================
// Map TV remote keys to actions
const TV_KEYS = {
    // Navigation
    'ArrowUp': 'scrollUp',
    'ArrowDown': 'scrollDown',
    'ArrowLeft': 'navLeft',
    'ArrowRight': 'navRight',
    'Enter': 'selectItem',
    'Backspace': 'goBack',
    'Escape': 'goBack',
    // Media
    'MediaPlayPause': 'togglePlayPause',
    'MediaStop': 'stopMedia',
    'MediaTrackNext': 'nextItem',
    'MediaTrackPrevious': 'prevItem',
    // Shortcuts (Number keys)
    '1': 'collection1',
    '2': 'collection2',
    '3': 'collection3',
    '4': 'collection4',
    '5': 'collection5',
    '6': 'collection6',
    '7': 'collection7',
    '8': 'collection8',
    '9': 'collection9',
    '0': 'allLinks',
    // Menu
    'Menu': 'toggleSidebar',
    'ContextMenu': 'toggleSidebar',
};

let selectedIndex = -1;
let isTVMode = false;

function detectTV() {
    // Check if it's a smart TV browser
    const userAgent = navigator.userAgent || '';
    const isTizen = userAgent.includes('Tizen');
    const isWebOS = userAgent.includes('WebOS');
    const isAndroidTV = userAgent.includes('Android') && userAgent.includes('TV');
    const isFireTV = userAgent.includes('FireTV') || userAgent.includes('AFT');
    const isAppleTV = userAgent.includes('AppleTV');
    
    return isTizen || isWebOS || isAndroidTV || isFireTV || isAppleTV;
}

function initializeTVMode() {
    isTVMode = detectTV();
    if (isTVMode) {
        console.log('🦊 TV Mode activated!');
        document.body.classList.add('tv-mode');
        // Show TV controls hint
        showTVHint();
    }
}

function showTVHint() {
    const hint = document.createElement('div');
    hint.id = 'tvHint';
    hint.style.cssText = `
        position: fixed;
        bottom: 60px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(0,0,0,0.8);
        color: #ff6600;
        padding: 10px 20px;
        border-radius: 30px;
        font-size: 12px;
        z-index: 9999;
        border: 1px solid #ff6600;
        backdrop-filter: blur(10px);
        transition: opacity 1s ease;
        text-align: center;
        font-family: monospace;
        pointer-events: none;
    `;
    hint.innerHTML = '📺 TV Remote: <span style="color:#88aaff;">↑↓</span> navigate • <span style="color:#88aaff;">OK</span> select • <span style="color:#88aaff;">Back</span> go back';
    document.body.appendChild(hint);
    
    // Auto-hide after 5 seconds
    setTimeout(() => {
        hint.style.opacity = '0';
        setTimeout(() => hint.remove(), 1000);
    }, 5000);
}

function handleTVKey(event) {
    const key = event.key;
    const action = TV_KEYS[key];
    
    if (!action) {
        // If not a TV key, ignore
        return;
    }
    
    event.preventDefault();
    event.stopPropagation();
    
    console.log('📺 TV Remote:', key, '→', action);
    
    switch(action) {
        case 'scrollUp':
            scrollLinks(-1);
            break;
        case 'scrollDown':
            scrollLinks(1);
            break;
        case 'navLeft':
            navLeft();
            break;
        case 'navRight':
            navRight();
            break;
        case 'selectItem':
            selectCurrentItem();
            break;
        case 'goBack':
            goBack();
            break;
        case 'togglePlayPause':
            toggleReadLaterCurrent();
            break;
        case 'toggleSidebar':
            toggleSidebar();
            break;
        case 'allLinks':
            switchCollection('all');
            break;
        case 'collection1':
        case 'collection2':
        case 'collection3':
        case 'collection4':
        case 'collection5':
        case 'collection6':
        case 'collection7':
        case 'collection8':
        case 'collection9':
            const num = parseInt(action.replace('collection', ''));
            switchToCollectionByIndex(num - 1);
            break;
        default:
            console.log('Unhandled TV action:', action);
    }
}

// ============================================================
// TV NAVIGATION HELPERS
// ============================================================
function getLinkElements() {
    return document.querySelectorAll('.link-card');
}

function scrollLinks(direction) {
    const links = getLinkElements();
    if (!links.length) return;
    
    // Remove previous highlight
    links.forEach(el => el.style.borderColor = '');
    
    selectedIndex = Math.max(0, Math.min(links.length - 1, selectedIndex + direction));
    
    const target = links[selectedIndex];
    if (target) {
        target.style.borderColor = '#ff6600';
        target.style.boxShadow = '0 0 20px #ff6600';
        target.scrollIntoView({ block: 'center', behavior: 'smooth' });
        
        // Update status
        const title = target.querySelector('h3')?.textContent || 'Link';
        showTVStatus('📍 ' + title);
    }
}

function navLeft() {
    // Go to previous collection
    const collections = getAllCollections();
    const currentIndex = collections.indexOf(getCurrentFilter());
    if (currentIndex > 0) {
        switchToCollection(collections[currentIndex - 1]);
    }
}

function navRight() {
    // Go to next collection
    const collections = getAllCollections();
    const currentIndex = collections.indexOf(getCurrentFilter());
    if (currentIndex < collections.length - 1) {
        switchToCollection(collections[currentIndex + 1]);
    }
}

function selectCurrentItem() {
    const links = getLinkElements();
    if (selectedIndex >= 0 && selectedIndex < links.length) {
        const link = links[selectedIndex];
        const url = link.querySelector('h3 a')?.href;
        if (url) {
            window.open(url, '_blank');
        }
    }
}

function toggleReadLaterCurrent() {
    const links = getLinkElements();
    if (selectedIndex >= 0 && selectedIndex < links.length) {
        const link = links[selectedIndex];
        const id = link.dataset.id;
        if (id) {
            toggleReadLater(id);
        }
    }
}

function switchToCollection(collection) {
    // Find the nav item for this collection
    const navItems = document.querySelectorAll('.nav-item');
    for (let item of navItems) {
        if (item.dataset.panel === collection || item.textContent.trim() === collection) {
            item.click();
            break;
        }
    }
    showTVStatus('📂 ' + collection);
}

function switchToCollectionByIndex(index) {
    const collections = getAllCollections();
    if (index >= 0 && index < collections.length) {
        switchToCollection(collections[index]);
    }
}

function getAllCollections() {
    const items = document.querySelectorAll('.nav-item[data-panel]');
    const collections = [];
    items.forEach(item => {
        const panel = item.dataset.panel;
        if (panel !== 'all' && panel !== 'collections' && panel !== 'read-later' && panel !== 'settings') {
            collections.push(panel);
        }
    });
    // Add special views
    return ['all', 'read-later', ...collections];
}

function goBack() {
    const activePanel = document.querySelector('.panel.active');
    if (activePanel && activePanel.id !== 'panel-all
