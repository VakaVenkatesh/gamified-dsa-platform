function showToast(message, type = 'info', levelDirection = null) {
    // Choose container based on whether it's a level toast
    const container = levelDirection ? 
        document.getElementById('level-toast-container') : 
        document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.classList.add('toast');

    switch (type) {
        case 'success': toast.classList.add('toast-success'); break;
        case 'error':   toast.classList.add('toast-danger'); break;
        case 'warning': toast.classList.add('toast-warning'); break;
        default:        toast.classList.add('toast-info'); break;
    }

    // Level-specific styles
    if (levelDirection === 'up') toast.classList.add('level-up');
    else if (levelDirection === 'down') toast.classList.add('level-down');

    toast.textContent = message;
    container.prepend(toast);

    setTimeout(() => toast.classList.add('show'), 50);

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

// Show Flask flash messages as toasts
document.addEventListener('DOMContentLoaded', () => {
    if (window.initialMessages && window.initialMessages.length > 0) {
        window.initialMessages.forEach(msg => {
            showToast(msg.message, msg.category);
        });
    }
});

function updateHeader(progress, levelChanged = false) {
    const header = document.querySelector('header');
    if (!header) return;

    const levelBar = header.querySelector('#header-level-bar');
    const xpFill = header.querySelector('#header-xp-fill');

    if (levelBar && xpFill && progress) {
        levelBar.textContent = `Level ${progress.level} | ${progress.xp}/${progress.level*200} XP`;

        let xpInCurrentLevel = progress.xp - (progress.level - 1) * 200;

        if (levelChanged) {
            // Reset XP bar to 0 immediately
            xpFill.style.width = `0%`;
            // Then animate to current XP percentage
            setTimeout(() => {
                const xpPercentage = (xpInCurrentLevel / 200) * 100;
                xpFill.style.width = `${xpPercentage}%`;
            }, 100); // slight delay for animation effect
        } else {
            const xpPercentage = (xpInCurrentLevel / 200) * 100;
            xpFill.style.width = `${xpPercentage}%`;
        }
    }
}

// Handle marking problems complete/incomplete
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.problem-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', async (event) => {
            const problemKey = event.target.dataset.problemKey;

            try {
                const response = await fetch('/toggle_problem', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ problem_key: problemKey })
                });

                let data = await response.json();

                if (!response.ok || !data.success) {
                    const errMsg = data && (data.error || data.message) ? (data.error || data.message) : `Server error ${response.status}`;
                    showToast(errMsg, 'error');
                    return;
                }

                // Show all messages
                if (Array.isArray(data.messages)) {
                    data.messages.forEach(msg => showToast(msg.message, msg.category, msg.level_message));
                }

                // Show earned achievements (always bottom-right)
                if (Array.isArray(data.earned_achievements)) {
                    data.earned_achievements.forEach(ach => {
                        showToast(`🏆 Achievement Unlocked: ${ach.name}`, 'success');
                    });
                }

                // Update header progress
                if (data.progress) updateHeader(data.progress);

                // Update row UI
                const row = event.target.closest('tr');
                if (row) {
                    if (data.action === 'completed') row.classList.add('problem-completed');
                    else row.classList.remove('problem-completed');
                }

            } catch (err) {
                console.error('Fetch error:', err);
                showToast('Error communicating with server', 'error');
            }
        });
    });
});
