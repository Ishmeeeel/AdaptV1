-- ============================================================
-- AdaptAble – Supabase Database Schema
-- Run this in the Supabase SQL Editor (or via supabase db push)
-- ============================================================

-- ── Extensions ────────────────────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── Schools ───────────────────────────────────────────────────────────────
create table if not exists public.schools (
    id          uuid primary key default uuid_generate_v4(),
    name        text not null,
    location    text not null default '',
    access_code text not null unique,
    is_active   boolean not null default true,
    created_at  timestamptz not null default now()
);

-- ── Profiles (extends auth.users) ────────────────────────────────────────
create table if not exists public.profiles (
    id                  uuid primary key references auth.users(id) on delete cascade,
    full_name           text not null,
    email               text not null,
    role                text not null check (role in ('student','teacher','admin')) default 'student',
    school_id           uuid references public.schools(id),
    disability_profile  text check (disability_profile in ('visual','hearing','dyslexia','motor','none')) default 'none',
    language            text check (language in ('english','hausa','yoruba','igbo')) default 'english',
    font_size           text not null default 'medium',
    voice_speed         text not null default 'normal',
    high_contrast       boolean not null default false,
    onboarding_complete boolean not null default false,
    avatar_url          text,
    created_at          timestamptz not null default now()
);

-- ── Lessons ───────────────────────────────────────────────────────────────
create table if not exists public.lessons (
    id                 uuid primary key default uuid_generate_v4(),
    teacher_id         uuid not null references public.profiles(id),
    school_id          uuid references public.schools(id),
    title              text not null,
    subject            text not null,
    page_count         int not null default 0,
    icon_emoji         text not null default '📖',
    is_published       boolean not null default false,
    processing_status  text not null default 'pending'
                           check (processing_status in ('pending','running','done','failed')),
    storage_path       text,
    created_at         timestamptz not null default now()
);

-- ── Lesson pages ──────────────────────────────────────────────────────────
create table if not exists public.lesson_pages (
    id                  uuid primary key default uuid_generate_v4(),
    lesson_id           uuid not null references public.lessons(id) on delete cascade,
    page_number         int not null,
    content_original    text,
    content_simplified  text,
    image_description   text,
    unique (lesson_id, page_number)
);

-- ── Lesson audio ──────────────────────────────────────────────────────────
create table if not exists public.lesson_audio (
    id         uuid primary key default uuid_generate_v4(),
    lesson_id  uuid not null references public.lessons(id) on delete cascade,
    language   text not null,
    audio_url  text not null,
    unique (lesson_id, language)
);

-- ── Student–Lesson progress ───────────────────────────────────────────────
create table if not exists public.student_lessons (
    id               uuid primary key default uuid_generate_v4(),
    student_id       uuid not null references public.profiles(id) on delete cascade,
    lesson_id        uuid not null references public.lessons(id) on delete cascade,
    current_page     int not null default 1,
    progress_percent int not null default 0,
    is_completed     boolean not null default false,
    enrolled_at      timestamptz not null default now(),
    last_accessed_at timestamptz,
    unique (student_id, lesson_id)
);

-- ── Processing jobs ───────────────────────────────────────────────────────
create table if not exists public.processing_jobs (
    id            uuid primary key default uuid_generate_v4(),
    lesson_id     uuid not null references public.lessons(id) on delete cascade unique,
    status        text not null default 'pending'
                      check (status in ('pending','running','done','failed')),
    steps         jsonb not null default '{
        "extract_text": false,
        "audio_english": false,
        "audio_hausa": false,
        "audio_yoruba": false,
        "audio_igbo": false,
        "simplify_dyslexia": false,
        "image_descriptions": false
    }'::jsonb,
    error_message text,
    created_at    timestamptz not null default now()
);

-- ── Activity log ──────────────────────────────────────────────────────────
create table if not exists public.activity_log (
    id           uuid primary key default uuid_generate_v4(),
    user_id      uuid not null references public.profiles(id) on delete cascade,
    action       text not null,
    lesson_id    uuid references public.lessons(id) on delete set null,
    lesson_title text,
    created_at   timestamptz not null default now()
);

-- ── Teacher notes ─────────────────────────────────────────────────────────
create table if not exists public.teacher_notes (
    id         uuid primary key default uuid_generate_v4(),
    teacher_id uuid not null references public.profiles(id) on delete cascade,
    student_id uuid not null references public.profiles(id) on delete cascade,
    note_text  text not null,
    updated_at timestamptz not null default now(),
    unique (teacher_id, student_id)
);

-- ── Row Level Security ────────────────────────────────────────────────────
-- Enable RLS on all tables (backend uses service role key → bypasses RLS)
alter table public.schools        enable row level security;
alter table public.profiles       enable row level security;
alter table public.lessons        enable row level security;
alter table public.lesson_pages   enable row level security;
alter table public.lesson_audio   enable row level security;
alter table public.student_lessons enable row level security;
alter table public.processing_jobs enable row level security;
alter table public.activity_log   enable row level security;
alter table public.teacher_notes  enable row level security;

-- Students can read their own profile
create policy "students_read_own_profile" on public.profiles
    for select using (auth.uid() = id);

-- Students can read published lessons in their school
create policy "students_read_lessons" on public.lessons
    for select using (
        is_published = true
        and school_id = (select school_id from public.profiles where id = auth.uid())
    );

-- Students can read their own progress
create policy "students_read_own_progress" on public.student_lessons
    for select using (student_id = auth.uid());

-- ── Supabase Storage buckets (create manually in dashboard) ──────────────
-- Bucket: "lesson-files"   (private)
-- Bucket: "lesson-audio"   (public)
