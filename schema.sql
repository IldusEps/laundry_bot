-- Схема БД бота записи на стирку (MySQL 5.7.8+ / 8.x, MariaDB 10.2+)
-- Таблицы создаются автоматически при запуске бота, файл можно выполнить и вручную:
--   mysql -u laundry_bot -p laundry_bot < schema.sql

CREATE TABLE IF NOT EXISTS users (
    id               INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    telegram_id      BIGINT            NOT NULL,
    username         VARCHAR(64)       NULL,
    surname          VARCHAR(64)       NULL,
    room             SMALLINT UNSIGNED NULL,
    floor            TINYINT UNSIGNED  NULL,
    wing             TINYINT UNSIGNED  NULL COMMENT 'крыло, только для 5 этажа',
    role             ENUM('resident', 'starosta') NOT NULL DEFAULT 'resident',
    starosta_pending TINYINT(1)        NOT NULL DEFAULT 0 COMMENT 'заявка на старосту ждёт админа',
    is_admin         TINYINT(1)        NOT NULL DEFAULT 0,
    is_banned        TINYINT(1)        NOT NULL DEFAULT 0 COMMENT 'исключён старостой из записи',
    banned_by        INT UNSIGNED      NULL,
    banned_at        DATETIME          NULL,
    created_at       DATETIME          NOT NULL,
    updated_at       DATETIME          NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_telegram (telegram_id),
    KEY ix_users_room (room),
    KEY ix_users_floor (floor)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS room_bans (
    room       SMALLINT UNSIGNED NOT NULL,
    floor      TINYINT UNSIGNED  NOT NULL,
    banned_by  INT UNSIGNED      NULL,
    created_at DATETIME          NOT NULL,
    PRIMARY KEY (room),
    KEY ix_room_bans_floor (floor)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bookings (
    id           INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    user_id      INT UNSIGNED      NOT NULL,
    floor        TINYINT UNSIGNED  NOT NULL,
    room         SMALLINT UNSIGNED NOT NULL,
    slot_date    DATE              NOT NULL,
    slot_start   TIME              NOT NULL,
    slot_end     TIME              NOT NULL,
    status       ENUM('active', 'cancelled') NOT NULL DEFAULT 'active',
    -- 1 для активной записи, NULL для отменённой: уникальный индекс не даёт
    -- занять одно время дважды, но не мешает хранить историю отмен
    active_flag  TINYINT AS (IF(status = 'active', 1, NULL)) STORED,
    created_at   DATETIME          NOT NULL,
    cancelled_at DATETIME          NULL,
    cancelled_by INT UNSIGNED      NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_bookings_slot (floor, slot_date, slot_start, active_flag),
    KEY ix_bookings_user_date (user_id, slot_date),
    KEY ix_bookings_room_date (room, slot_date),
    CONSTRAINT fk_bookings_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS closures (
    id         INT UNSIGNED     NOT NULL AUTO_INCREMENT,
    floor      TINYINT UNSIGNED NOT NULL,
    date_from  DATE             NOT NULL,
    date_to    DATE             NOT NULL,
    time_from  TIME             NOT NULL,
    time_to    TIME             NOT NULL,
    reason     VARCHAR(255)     NULL,
    created_by INT UNSIGNED     NULL,
    created_at DATETIME         NOT NULL,
    PRIMARY KEY (id),
    KEY ix_closures_floor_dates (floor, date_from, date_to)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Заявки на смену фамилии/комнаты: изменения вступают в силу после одобрения старостой
CREATE TABLE IF NOT EXISTS change_requests (
    id          INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    user_id     INT UNSIGNED      NOT NULL,
    old_surname VARCHAR(64)       NULL,
    old_room    SMALLINT UNSIGNED NULL,
    old_floor   TINYINT UNSIGNED  NULL,
    new_surname VARCHAR(64)       NOT NULL,
    new_room    SMALLINT UNSIGNED NOT NULL,
    new_floor   TINYINT UNSIGNED  NOT NULL,
    new_wing    TINYINT UNSIGNED  NULL,
    status      ENUM('pending', 'approved', 'rejected', 'cancelled') NOT NULL DEFAULT 'pending',
    decided_by  INT UNSIGNED      NULL,
    created_at  DATETIME          NOT NULL,
    decided_at  DATETIME          NULL,
    PRIMARY KEY (id),
    KEY ix_change_requests_status (status, old_floor, new_floor),
    CONSTRAINT fk_change_requests_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
