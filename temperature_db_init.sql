-- 온도 로그 테이블 (ingps DB에 추가)
CREATE TABLE IF NOT EXISTS temperature_log (
    id         BIGINT      NOT NULL AUTO_INCREMENT,
    device_id  VARCHAR(64) NOT NULL,
    temp1      FLOAT       NOT NULL,
    temp2      FLOAT,
    angle_x    FLOAT,
    angle_y    FLOAT,
    angle_z    FLOAT,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_device_created (device_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
