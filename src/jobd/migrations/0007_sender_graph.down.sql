DROP INDEX IF EXISTS message_recipient_addresses_idx;
DROP INDEX IF EXISTS message_sender_domain_idx;
DROP INDEX IF EXISTS message_sender_address_idx;

ALTER TABLE message
    DROP COLUMN IF EXISTS recipient_addresses,
    DROP COLUMN IF EXISTS sender_domain,
    DROP COLUMN IF EXISTS sender_address;
