// IndexedDB wrapper for offline Q&A cache
const OfflineDB = {
    dbName: 'revisionaid-offline',
    version: 1,
    _db: null,

    async open() {
        if (this._db) return this._db;
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(this.dbName, this.version);
            request.onupgradeneeded = (e) => {
                const db = e.target.result;
                if (!db.objectStoreNames.contains('questions')) {
                    const store = db.createObjectStore('questions', { keyPath: 'id' });
                    store.createIndex('subject_id', 'subject_id');
                    store.createIndex('next_review_date', 'next_review_date');
                }
                if (!db.objectStoreNames.contains('pending_answers')) {
                    db.createObjectStore('pending_answers', { keyPath: 'id', autoIncrement: true });
                }
            };
            request.onsuccess = (e) => {
                this._db = e.target.result;
                resolve(this._db);
            };
            request.onerror = (e) => reject(e.target.error);
        });
    },

    async syncQuestions() {
        try {
            const questions = await API.get('/questions/export');
            const db = await this.open();
            const tx = db.transaction('questions', 'readwrite');
            const store = tx.objectStore('questions');
            store.clear();
            for (const q of questions) {
                store.put(q);
            }
            await new Promise((resolve, reject) => {
                tx.oncomplete = resolve;
                tx.onerror = () => reject(tx.error);
            });
            console.log(`Synced ${questions.length} questions to offline cache`);
        } catch (e) {
            console.warn('Offline sync failed:', e);
        }
    },

    async getQuizCards(subjectId, count = 20) {
        const db = await this.open();
        const tx = db.transaction('questions', 'readonly');
        const store = tx.objectStore('questions');
        const all = await new Promise((resolve, reject) => {
            const req = store.getAll();
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });

        let filtered = subjectId ? all.filter(q => q.subject_id == subjectId) : all;

        // Sort by next_review_date (earliest first), then shuffle
        const today = new Date().toISOString().split('T')[0];
        const due = filtered.filter(q => !q.next_review_date || q.next_review_date <= today);
        const shuffled = due.sort(() => Math.random() - 0.5);
        return shuffled.slice(0, count);
    },

    async savePendingAnswer(answer) {
        const db = await this.open();
        const tx = db.transaction('pending_answers', 'readwrite');
        tx.objectStore('pending_answers').add(answer);
        await new Promise((resolve, reject) => {
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
    },

    async syncPendingAnswers() {
        try {
            const db = await this.open();
            const tx = db.transaction('pending_answers', 'readonly');
            const store = tx.objectStore('pending_answers');
            const pending = await new Promise((resolve, reject) => {
                const req = store.getAll();
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => reject(req.error);
            });

            if (pending.length === 0) return;

            for (const answer of pending) {
                try {
                    await API.post('/quiz/' + answer.session_id + '/answer', answer);
                } catch (e) {
                    console.warn('Failed to sync answer:', e);
                }
            }

            // Clear synced answers
            const clearTx = db.transaction('pending_answers', 'readwrite');
            clearTx.objectStore('pending_answers').clear();
        } catch (e) {
            console.warn('Pending answer sync failed:', e);
        }
    }
};
