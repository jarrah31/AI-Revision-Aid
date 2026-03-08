// Main Alpine.js application
document.addEventListener('alpine:init', () => {
    // Global app store
    Alpine.store('app', {
        user: API.getUser(),
        loading: false,
        toast: null,
        toastTimeout: null,
        route: '',   // current route name, set by Router on each navigation

        // Exchange rate (USD → GBP), updated on init
        gbpRate: 0.79,
        gbpRateDate: '',

        get isLoggedIn() {
            return !!this.user;
        },

        get isAdmin() {
            return this.user && this.user.is_admin;
        },

        /**
         * Format a USD value as GBP using the current exchange rate.
         * Pass showDash=true to return '—' for zero (useful in admin tables).
         */
        fmtGBP(usd, showDash = false) {
            if (usd === null || usd === undefined) return '—';
            if (usd === 0) return showDash ? '—' : '£0.00';
            const gbp = usd * this.gbpRate;
            if (gbp < 0.001) return '< £0.001';
            return '£' + gbp.toFixed(4);
        },

        showToast(message, type = 'info') {
            this.toast = { message, type };
            if (this.toastTimeout) clearTimeout(this.toastTimeout);
            this.toastTimeout = setTimeout(() => { this.toast = null; }, 3000);
        },

        logout() {
            API.clearToken();
            this.user = null;
            window.location.hash = '#login';
        },

        setUser(user) {
            this.user = user;
            API.setUser(user);
        }
    });
});

// Initialize router after Alpine loads
document.addEventListener('alpine:initialized', () => {
    Router.init();

    // Fetch live USD→GBP rate (no auth required)
    fetch('/api/costs/rate')
        .then(r => r.json())
        .then(d => {
            Alpine.store('app').gbpRate = d.usd_to_gbp;
            Alpine.store('app').gbpRateDate = d.date || '';
        })
        .catch(() => { /* keep default fallback rate */ });

    // Sync offline data when online
    if (API.isLoggedIn() && navigator.onLine) {
        OfflineDB.syncQuestions();
        OfflineDB.syncPendingAnswers();
    }

    // Re-sync when coming back online
    window.addEventListener('online', () => {
        if (API.isLoggedIn()) {
            OfflineDB.syncQuestions();
            OfflineDB.syncPendingAnswers();
        }
    });
});
