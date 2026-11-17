-- DR03 additive fencing/snapshot fields. Old attempts stay unauthorized/retained.
ALTER TABLE notification_delivery_attempts ADD COLUMN IF NOT EXISTS authorization_token UUID;
ALTER TABLE notification_delivery_attempts ADD COLUMN IF NOT EXISTS authorization_snapshot JSONB;
ALTER TABLE notification_delivery_history ADD COLUMN IF NOT EXISTS authorization_token UUID;
ALTER TABLE notification_delivery_history ADD COLUMN IF NOT EXISTS authorization_snapshot JSONB;
CREATE OR REPLACE FUNCTION trace_notification_delivery() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO notification_delivery_history(attempt_id,state,reason,authorization_token,authorization_snapshot)
    VALUES(NEW.id,NEW.state,NEW.reason,NEW.authorization_token,NEW.authorization_snapshot);
    RETURN NEW;
END;
$$;
