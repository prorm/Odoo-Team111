-- Live proof for resource_bookings_range_overlap_excl (migration
-- 010_booking_exclusion_predicate).
--
-- Run against a migrated Harmonix360 database, e.g.:
--   docker exec -i harmonix360_postgres psql -U postgres -d harmonix360_core \
--       -v ON_ERROR_STOP=0 -f - < verify_booking_exclusion.sql
--
-- Everything happens inside one transaction that is ROLLED BACK at the end, so
-- the script leaves no rows behind and is safe to re-run.
--
-- Four cases, matching the four states the predicate has to distinguish:
--   A. active  + active        overlapping -> must be REJECTED (23P01)
--   B. active  + CANCELLED     overlapping -> must be ALLOWED
--   C. active  + soft-deleted  overlapping -> must be ALLOWED
--   D. CANCELLED + CANCELLED   overlapping -> must be ALLOWED

BEGIN;

\echo '=============================================================='
\echo 'CONSTRAINT UNDER TEST'
\echo '=============================================================='
SELECT pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE conname = 'resource_bookings_range_overlap_excl';

-- Fixtures: one user (FK target for resource_bookings.user_id) and four
-- distinct meeting rooms so the four cases cannot interfere with each other.
INSERT INTO users (id, public_id, email, password_hash, name)
VALUES (9001, 'usr_exclproof', 'exclproof@harmonix360.test', '!not-a-real-hash', 'Exclusion Proof User')
ON CONFLICT (id) DO NOTHING;

INSERT INTO meeting_rooms (id, public_id, name, is_bookable) VALUES
  (9001, 'room_proof_a', 'Proof Room A', true),
  (9002, 'room_proof_b', 'Proof Room B', true),
  (9003, 'room_proof_c', 'Proof Room C', true),
  (9004, 'room_proof_d', 'Proof Room D', true);

\echo ''
\echo '=============================================================='
\echo 'CASE A — active + active overlapping  (EXPECT: REJECTED 23P01)'
\echo '=============================================================='
-- Wrapped in a DO block so the expected failure is CAUGHT and reported as a
-- pass. Doing this with a bare INSERT + ON_ERROR_STOP=0 would print the error
-- and then continue into whatever line follows, which reads as if the insert
-- had succeeded.
SAVEPOINT case_a;
DO $$
DECLARE
    sqlstate_seen text;
    message_seen  text;
BEGIN
    INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status)
    VALUES ('bkg_a1', 'meeting_room', 9001, 9001, '2027-01-01 09:00+00', '2027-01-01 10:00+00', 'CONFIRMED');
    RAISE NOTICE 'first CONFIRMED booking inserted (09:00-10:00)';

    BEGIN
        INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status)
        VALUES ('bkg_a2', 'meeting_room', 9001, 9001, '2027-01-01 09:30+00', '2027-01-01 10:30+00', 'CONFIRMED');
        RAISE EXCEPTION 'CASE A FAILED: overlapping CONFIRMED booking was ACCEPTED';
    EXCEPTION WHEN exclusion_violation THEN
        GET STACKED DIAGNOSTICS
            sqlstate_seen = RETURNED_SQLSTATE,
            message_seen  = MESSAGE_TEXT;
        RAISE NOTICE 'second overlapping CONFIRMED booking REJECTED';
        RAISE NOTICE 'SQLSTATE = %  (23P01 = exclusion_violation)', sqlstate_seen;
        RAISE NOTICE 'MESSAGE  = %', message_seen;
        RAISE NOTICE 'CASE A PASSED';
    END;
END
$$;
ROLLBACK TO SAVEPOINT case_a;

\echo ''
\echo '=============================================================='
\echo 'CASE B — active + CANCELLED overlapping  (EXPECT: ALLOWED)'
\echo '=============================================================='
SAVEPOINT case_b;
INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status)
VALUES ('bkg_b1', 'meeting_room', 9002, 9001, '2027-01-01 09:00+00', '2027-01-01 10:00+00', 'CANCELLED');
INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status)
VALUES ('bkg_b2', 'meeting_room', 9002, 9001, '2027-01-01 09:30+00', '2027-01-01 10:30+00', 'CONFIRMED');
SELECT public_id, status, deleted_at, start_time, end_time
FROM resource_bookings WHERE resource_id = 9002 ORDER BY public_id;
\echo '-- both rows present above => CASE B PASSED'
RELEASE SAVEPOINT case_b;

\echo ''
\echo '=============================================================='
\echo 'CASE C — active + soft-deleted overlapping  (EXPECT: ALLOWED)'
\echo '=============================================================='
SAVEPOINT case_c;
INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status, deleted_at)
VALUES ('bkg_c1', 'meeting_room', 9003, 9001, '2027-01-01 09:00+00', '2027-01-01 10:00+00', 'CONFIRMED', now());
INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status)
VALUES ('bkg_c2', 'meeting_room', 9003, 9001, '2027-01-01 09:30+00', '2027-01-01 10:30+00', 'CONFIRMED');
SELECT public_id, status, (deleted_at IS NOT NULL) AS soft_deleted, start_time, end_time
FROM resource_bookings WHERE resource_id = 9003 ORDER BY public_id;
\echo '-- both rows present above (one soft-deleted, one live) => CASE C PASSED'
RELEASE SAVEPOINT case_c;

\echo ''
\echo '=============================================================='
\echo 'CASE D — CANCELLED + CANCELLED overlapping  (EXPECT: ALLOWED)'
\echo '=============================================================='
SAVEPOINT case_d;
INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status)
VALUES ('bkg_d1', 'meeting_room', 9004, 9001, '2027-01-01 09:00+00', '2027-01-01 10:00+00', 'CANCELLED');
INSERT INTO resource_bookings (public_id, resource_type, resource_id, user_id, start_time, end_time, status)
VALUES ('bkg_d2', 'meeting_room', 9004, 9001, '2027-01-01 09:30+00', '2027-01-01 10:30+00', 'CANCELLED');
SELECT public_id, status, start_time, end_time
FROM resource_bookings WHERE resource_id = 9004 ORDER BY public_id;
\echo '-- both rows present above => CASE D PASSED'
RELEASE SAVEPOINT case_d;

\echo ''
\echo '=============================================================='
\echo 'ROLLING BACK — verification leaves no rows behind'
\echo '=============================================================='
ROLLBACK;
