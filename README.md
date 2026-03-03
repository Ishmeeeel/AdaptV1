# AdaptAble Backend API

Production-ready **FastAPI** backend for [AdaptAble](https://adaptable.vercel.app) – an AI-powered accessible education platform for Nigerian students with diverse learning needs.

---

## Architecture

```
adaptable-backend/
├── main.py                     # App factory, CORS, routers
├── config.py                   # Pydantic Settings (env vars)
├── database.py                 # Supabase client singleton
├── dependencies.py             # JWT auth dependency
├── schemas.py                  # All Pydantic request/response models
├── routers/
│   ├── auth.py                 # /auth/*
│   ├── student.py              # /student/*
│   ├── teacher.py              # /teacher/*
│   └── admin.py                # /admin/*
├── services/
│   ├── auth_service.py         # Register, profile, settings
│   ├── student_service.py      # Dashboard, lessons, progress
│   ├── teacher_service.py      # Lesson upload, students
│   ├── admin_service.py        # Schools, platform stats
│   └── processing_service.py  # PDF extract → HF simplify → Azure TTS
├── migrations/
│   └── 001_initial_schema.sql  # Full Supabase schema + RLS
├── requirements.txt
├── render.yaml                 # Render.com deployment spec
└── .env.example
```

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/register` | ❌ | Create teacher/admin account |
| GET | `/auth/me` | ✅ | Get current user profile |
| PUT | `/auth/onboarding` | ✅ | Save onboarding selections |
| PUT | `/auth/settings` | ✅ | Update accessibility settings |
| POST | `/auth/logout` | ✅ | Server-side logout acknowledgement |
| GET | `/student/dashboard` | ✅ | Student dashboard stats |
| GET | `/student/lessons` | ✅ | All available lessons |
| GET | `/student/lessons/{id}` | ✅ | Single lesson |
| GET | `/student/lessons/{id}/page/{n}` | ✅ | Page content (original + simplified) |
| GET | `/student/lessons/{id}/audio` | ✅ | TTS audio URL |
| PUT | `/student/lessons/{id}/progress` | ✅ | Update reading progress |
| GET | `/student/progress` | ✅ | Full progress overview + activity log |
| GET | `/teacher/dashboard` | ✅ | Teacher dashboard stats |
| GET | `/teacher/lessons` | ✅ | Teacher's lessons |
| POST | `/teacher/lessons` | ✅ | Upload PDF lesson (multipart) |
| DELETE | `/teacher/lessons/{id}` | ✅ | Delete lesson |
| POST | `/teacher/lessons/{id}/assign` | ✅ | Assign lesson to students |
| GET | `/teacher/processing/{id}` | ✅ | Poll lesson processing status |
| GET | `/teacher/students` | ✅ | School's students |
| POST | `/teacher/students` | ✅ | Create student account |
| GET | `/teacher/students/{id}` | ✅ | Student detail + per-lesson progress |
| PUT | `/teacher/students/{id}/notes` | ✅ | Save teacher note |
| GET | `/admin/dashboard` | ✅ | Platform-wide stats |
| GET | `/admin/schools` | ✅ | All schools |
| POST | `/admin/schools` | ✅ | Create school |
| POST | `/admin/schools/{id}/access-code` | ✅ | Regenerate access code |
| GET | `/health` | ❌ | Render health-check probe |

---

## Lesson Processing Pipeline

When a teacher uploads a PDF lesson, the backend fires a **background task** that:

1. **Extracts text** per page using `pdfplumber`
2. **Simplifies content** using the Mistral-7B model via HuggingFace Inference API (for dyslexia/cognitive profiles)
3. **Generates image descriptions** (placeholder – hookable to a vision model)
4. **Synthesises TTS audio** in 4 languages (English, Hausa, Yoruba, Igbo) using Azure Neural TTS and stores MP3s in Supabase Storage
5. Updates `processing_jobs.steps` flags after each step — the teacher can poll `/teacher/processing/{lesson_id}` to show a live progress UI

---

## Local Development

### Prerequisites
- Python 3.10+
- A Supabase project with the schema from `migrations/001_initial_schema.sql` applied
- (Optional) Azure Cognitive Services subscription for TTS
- (Optional) HuggingFace account + API token

### Setup

```bash
# 1. Clone and enter the directory
cd adaptable-backend

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your real keys

# 5. Run the dev server
uvicorn main:app --reload --port 8000
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive Swagger UI.

---

## Supabase Setup

1. Create a new Supabase project at [supabase.com](https://supabase.com)
2. Open the **SQL Editor** and run the contents of `migrations/001_initial_schema.sql`
3. Create two **Storage buckets** in the Supabase dashboard:
   - `lesson-files` – **Private** (stores uploaded PDFs)
   - `lesson-audio` – **Public** (stores generated MP3s)
4. Copy your project values into `.env`:
   - `SUPABASE_URL` → Project Settings → API → Project URL
   - `SUPABASE_SERVICE_ROLE_KEY` → Project Settings → API → service_role key
   - `SUPABASE_JWT_SECRET` → Project Settings → API → JWT Secret

---

## Deployment on Render

1. Push the `adaptable-backend/` directory to a GitHub repository
2. Create a new **Web Service** on [render.com](https://render.com) pointing to the repo
3. Render will detect `render.yaml` automatically
4. Set the secret environment variables in the Render dashboard (marked `sync: false`):
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `SUPABASE_JWT_SECRET`
   - `AZURE_TTS_KEY`
   - `HF_TOKEN`
5. Update `ALLOWED_ORIGINS_STR` with your Vercel frontend URL

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | ✅ | `https://yourproject.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | Service-role key (bypasses RLS) |
| `SUPABASE_JWT_SECRET` | ✅ | From Supabase → Settings → API → JWT Secret |
| `AZURE_TTS_KEY` | ⚠️ | Azure Cognitive Services key (TTS disabled if missing) |
| `AZURE_TTS_REGION` | ⚠️ | Azure region, e.g. `eastus` |
| `HF_TOKEN` | ⚠️ | HuggingFace API token (simplification skipped if missing) |
| `HF_MODEL` | ➖ | Defaults to `mistralai/Mistral-7B-Instruct-v0.1` |
| `ALLOWED_ORIGINS_STR` | ✅ | Comma-separated CORS origins |

---

## Security Notes

- The backend **never** exposes the `SUPABASE_SERVICE_ROLE_KEY` to clients
- All protected routes validate the Supabase-issued JWT using `SUPABASE_JWT_SECRET`
- Row Level Security is enabled on all tables; the backend uses the service-role key to bypass it intentionally (server-side trust model)
- Admin role is verified server-side on every admin endpoint

---

## Frontend Integration

Set the following in your Next.js `.env.local`:

```env
NEXT_PUBLIC_BACKEND_URL=https://adaptable-backend.onrender.com
NEXT_PUBLIC_SUPABASE_URL=https://yourproject.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_anon_key
```
