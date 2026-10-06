-- Та же схема, что в schema.sql, на диалекте SQLite — только для тестов.
-- Соответствие колонок проверяет tests/test_schema.py: при изменении schema.sql поправьте и этот файл.
-- DATE и TIME хранятся строками ISO (их разбирает laundry.db._fix), DATETIME — через конвертер адаптера.

CREATE TABLE users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id      INTEGER NULL UNIQUE,
    vk_id            INTEGER NULL UNIQUE,
    username         TEXT NULL,
    surname          TEXT NULL,
    room             TEXT NULL,
    floor            INTEGER NULL,
    wing             INTEGER NULL,
    role             TEXT NOT NULL DEFAULT 'resident' CHECK (role IN ('resident', 'starosta')),
    starosta_pending INTEGER NOT NULL DEFAULT 0,
    is_admin         INTEGER NOT NULL DEFAULT 0,
    is_banned        INTEGER NOT NULL DEFAULT 0,
    banned_by        INTEGER NULL,
    banned_at        DATETIME NULL,
    created_at       DATETIME NOT NULL,
    updated_at       DATETIME NOT NULL
);

CREATE TABLE room_bans (
    room       TEXT NOT NULL PRIMARY KEY,
    floor      INTEGER NOT NULL,
    banned_by  INTEGER NULL,
    created_at DATETIME NOT NULL
);

CREATE TABLE bookings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    floor        INTEGER NOT NULL,
    room         TEXT NOT NULL,
    slot_date    TEXT NOT NULL,
    slot_start   TEXT NOT NULL,
    slot_end     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    active_flag  INTEGER GENERATED ALWAYS AS (CASE WHEN status = 'active' THEN 1 END) STORED,
    created_at   DATETIME NOT NULL,
    cancelled_at DATETIME NULL,
    cancelled_by INTEGER NULL
);
-- как в MySQL: одно время нельзя занять дважды, отменённые записи (NULL) индексу не мешают
CREATE UNIQUE INDEX uq_bookings_slot ON bookings (floor, slot_date, slot_start, active_flag);

CREATE TABLE closures (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    floor      INTEGER NOT NULL,
    date_from  TEXT NOT NULL,
    date_to    TEXT NOT NULL,
    time_from  TEXT NOT NULL,
    time_to    TEXT NOT NULL,
    reason     TEXT NULL,
    created_by INTEGER NULL,
    created_at DATETIME NOT NULL
);

CREATE TABLE change_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    old_surname TEXT NULL,
    old_room    TEXT NULL,
    old_floor   INTEGER NULL,
    new_surname TEXT NOT NULL,
    new_room    TEXT NOT NULL,
    new_floor   INTEGER NOT NULL,
    new_wing    INTEGER NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    decided_by  INTEGER NULL,
    created_at  DATETIME NOT NULL,
    decided_at  DATETIME NULL
);

CREATE TABLE link_codes (
    code       TEXT NOT NULL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    platform   TEXT NOT NULL CHECK (platform IN ('tg', 'vk')),
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL
);
