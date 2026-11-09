-- Migration: Add Multi-Year Breakout (MYB) tracking columns to historical_data
ALTER TABLE historical_data 
    ADD COLUMN myb_stage VARCHAR(16) DEFAULT NULL,
    ADD COLUMN myb_level DECIMAL(14,4) DEFAULT NULL,
    ADD COLUMN myb_dist_pct DOUBLE DEFAULT NULL,
    ADD COLUMN myb_bo_date DATE DEFAULT NULL,
    ADD COLUMN myb_age_bars INT DEFAULT NULL;
