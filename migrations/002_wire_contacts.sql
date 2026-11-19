-- Keep old region hits; new grid hits store one footprint and one force per hit.
ALTER TABLE hits ALTER COLUMN region DROP NOT NULL;
ALTER TABLE hits ADD COLUMN contact jsonb;
ALTER TABLE hits ADD CONSTRAINT hit_location_kind CHECK (
    (region IS NOT NULL AND contact IS NULL) OR
    (region IS NULL AND contact IS NOT NULL AND jsonb_typeof(contact) = 'object')
);
