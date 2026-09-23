# Mail Header Extractor V4.2

نسخة V4.2 مصلحة خصيصاً لـ Vercel.

## أهم إصلاح

في النسخة السابقة كان `api/index.py` منفصل على frontend مبني بـ Vite، وظهر:

`404 NOT_FOUND` عند فتح `/api/health`.

في V4.2 أصبح `app.py` في جذر المشروع وهو FastAPI entrypoint الرئيسي، والواجهة React/Vite يتم بناؤها إلى `public/`.

Vercel يدعم FastAPI مباشرة من `app.py`/`index.py`، والملفات الموجودة في `public/` يتم تقديمها كـ static assets.

## الهيكلة

```text
mail-header-extractor-v4.2/
├── app.py
├── requirements.txt
├── vercel.json
├── README.md
└── frontend/
    ├── index.html
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    └── src/
        ├── main.tsx
        └── style.css
```

## Vercel

1. ارفع محتويات هذا المشروع إلى GitHub.
2. افتح Vercel.
3. Import Project من GitHub.
4. Root Directory: `./`
5. لا تحتاج تضيف Build Command يدوياً؛ `vercel.json` فيه الأمر.
6. Deploy.

بعد الـ Deploy جرّب:

```text
https://YOUR-DOMAIN.vercel.app/api/health
```

النتيجة الصحيحة:

```json
{
  "ok": true,
  "version": "4.2.0",
  "platform": "vercel-fastapi"
}
```

## Gmail

- Provider: Gmail
- Custom IMAP Host: خليه فارغ
- Email: Gmail الكامل
- App Password: Google App Password، وليس كلمة سر Gmail العادية

## Outlook

- Provider: Outlook / Microsoft 365
- Custom IMAP Host: خليه فارغ

## Yahoo

- Provider: Yahoo
- Custom IMAP Host: خليه فارغ

## iCloud

- Provider: iCloud
- Custom IMAP Host: خليه فارغ

## Custom IMAP

إذا كان عندك مزود آخر، اختر `Custom IMAP` واكتب:

```text
imap.example.com
```

والمنفذ الافتراضي:

```text
993
```

## Environment Variable اختياري

يمكنك إضافة:

```text
APP_ACCESS_PASSWORD
```

في Vercel Environment Variables.

إذا أضفتها، سيظهر حقل إضافي في التطبيق لحماية API.

## الخصوصية

- بيانات mailbox لا يتم حفظها في قاعدة بيانات.
- لا توجد SQLite / Redis / PostgreSQL.
- بيانات الدخول تستخدم فقط أثناء طلب IMAP.
- لا تسجل كلمات المرور في التطبيق.

## الحقول

From, Sender, Subject, To, Cc, Date, Message-ID, Return-Path,
Content-Type, Reply-To, Client-IP, Received, Authentication-Results,
DKIM, SPF, DMARC.

## Export

- CSV
- XLSX
- JSON
- TXT

## Local development

Python:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Frontend:

```bash
cd frontend
npm install
npm run build
cd ..
```

ثم:

```bash
uvicorn app:app --reload
```

الـ API سيكون:

```text
http://127.0.0.1:8000/api/health
```

ملاحظة: تشغيل الواجهة محلياً بشكل كامل مع نفس الـ origin يمكن عمله عبر Vercel CLI:

```bash
npm install -g vercel
vercel dev
```
