-- ============================================================================
-- IOAM Harness — 数据库初始化脚本
-- Phase 1: Core infrastructure tables
--
-- 使用方式:
--   mysql -u root -p < scripts/init_db.sql
--
-- 幂等设计: 使用 IF NOT EXISTS，可安全重复执行
-- ============================================================================

-- 创建数据库
CREATE DATABASE IF NOT EXISTS `ioam_harness`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `ioam_harness`;

-- ----------------------------------------------------------------------------
-- 用户表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
    `id`          BIGINT AUTO_INCREMENT PRIMARY KEY,
    `username`    VARCHAR(64)  NOT NULL,
    `password`    VARCHAR(256) NOT NULL COMMENT 'bcrypt hash',
    `email`       VARCHAR(128) NOT NULL DEFAULT '',
    `avatar`      VARCHAR(512) NOT NULL DEFAULT '',
    `role`        ENUM('user', 'admin') NOT NULL DEFAULT 'user',
    `is_active`   TINYINT(1) NOT NULL DEFAULT 1,
    `created_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE INDEX `idx_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 会话表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `conversations` (
    `id`            BIGINT AUTO_INCREMENT PRIMARY KEY,
    `user_id`       BIGINT NOT NULL,
    `session_id`    VARCHAR(64) NOT NULL COMMENT '前端 session 唯一标识',
    `title`         VARCHAR(256) NOT NULL DEFAULT '' COMMENT '自动生成的会话标题',
    `model`         VARCHAR(64) NOT NULL DEFAULT 'qwen-max',
    `message_count` INT NOT NULL DEFAULT 0,
    `created_at`    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE INDEX `idx_session` (`session_id`),
    INDEX `idx_user_conv` (`user_id`, `updated_at` DESC),
    CONSTRAINT `fk_conv_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 消息表（完整持久化）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `messages` (
    `id`              BIGINT AUTO_INCREMENT PRIMARY KEY,
    `conversation_id` BIGINT NOT NULL,
    `user_id`         BIGINT NOT NULL,
    `role`            ENUM('user', 'assistant', 'system', 'tool') NOT NULL,
    `content`         TEXT NOT NULL,
    `tool_calls`      JSON DEFAULT NULL COMMENT '工具调用记录 [{name, args, result}]',
    `token_count`     INT NOT NULL DEFAULT 0 COMMENT '该消息的 token 数',
    `metadata_`       JSON DEFAULT NULL COMMENT '扩展元数据',
    `created_at`      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX `idx_conv_time` (`conversation_id`, `created_at`),
    INDEX `idx_user` (`user_id`),
    CONSTRAINT `fk_msg_conv` FOREIGN KEY (`conversation_id`) REFERENCES `conversations` (`id`)
        ON DELETE CASCADE,
    CONSTRAINT `fk_msg_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
