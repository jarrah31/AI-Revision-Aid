// Simple hash-based SPA router
const Router = {
    routes: {
        'login': '/static/pages/login.html',
        'signup': '/static/pages/signup.html',
        'home': '/static/pages/home.html',
        'upload': '/static/pages/upload.html',
        'processing': '/static/pages/processing.html',
        'review': '/static/pages/review.html',
        'review-category': '/static/pages/review-category.html',
        'review-subcategory': '/static/pages/review-subcategory.html',
        'quiz': '/static/pages/quiz.html',
        'dashboard': '/static/pages/dashboard.html',
        'subjects': '/static/pages/subjects.html',
        'shared': '/static/pages/shared.html',
        'admin': '/static/pages/admin.html',
        'profile': '/static/pages/profile.html',
        'upload-history': '/static/pages/upload-history.html',
        'ocr-review': '/static/pages/ocr_review.html',
        'quiz-review': '/static/pages/quiz_review.html',
        'multi-processing': '/static/pages/multi-processing.html',
    },

    publicRoutes: ['login', 'signup'],
    adminRoutes: ['admin'],

    parseHash() {
        const hash = window.location.hash.slice(1) || 'home';
        const parts = hash.split('/');
        return { route: parts[0], params: parts.slice(1) };
    },

    async navigate() {
        const { route, params } = this.parseHash();

        // Auth guard
        if (!this.publicRoutes.includes(route) && !API.isLoggedIn()) {
            window.location.hash = '#login';
            return;
        }

        // Redirect logged-in users away from auth pages
        if (this.publicRoutes.includes(route) && API.isLoggedIn()) {
            window.location.hash = '#home';
            return;
        }

        // Admin guard
        if (this.adminRoutes.includes(route) && !API.isAdmin()) {
            window.location.hash = '#home';
            return;
        }

        const templateUrl = this.routes[route] || this.routes['home'];
        try {
            const html = await fetch(templateUrl + '?v=' + Date.now(), { cache: 'no-store' }).then(r => {
                if (!r.ok) throw new Error('Page not found');
                return r.text();
            });
            const container = document.getElementById('page-content');
            container.innerHTML = html;

            // Store params for Alpine components to access
            window._routeParams = params;

            // Execute any <script> tags in the loaded template so that
            // Alpine.data() component registrations run before initTree.
            // (innerHTML does not execute scripts automatically.)
            container.querySelectorAll('script').forEach(oldScript => {
                const newScript = document.createElement('script');
                newScript.textContent = oldScript.textContent;
                document.head.appendChild(newScript);
                oldScript.remove();
            });

            // Reinitialize Alpine on new content
            if (window.Alpine) {
                Alpine.store('app').route = route;
                Alpine.initTree(container);
            }
        } catch (e) {
            document.getElementById('page-content').innerHTML =
                '<div class="text-center py-12 text-gray-500">Page not found</div>';
        }
    },

    init() {
        window.addEventListener('hashchange', () => this.navigate());
        this.navigate();
    }
};
