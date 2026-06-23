-- ============================================================================
-- Sprint 6 (Q12) — document-type catalog: maps every doc_type to a human label
-- and a business transaction family (Order-to-Cash, Procure-to-Pay,
-- Transportation, Warehouse, Functional, Air). Drives the "by message type,
-- grouped by transaction type" command center. Idempotent.
-- ============================================================================
CREATE TABLE IF NOT EXISTS doc_type_catalog (
  doc_type          text PRIMARY KEY,
  label             text,
  business_family   text,      -- Order-to-Cash | Procure-to-Pay | Transportation | Warehouse | Functional | Air
  typical_direction text,      -- in | out | both
  sla_minutes       int        -- response/processing SLA hint (nullable)
);

INSERT INTO doc_type_catalog (doc_type, label, business_family, typical_direction, sla_minutes) VALUES
 -- Order-to-Cash
 ('850','850 Purchase Order',        'Order-to-Cash',   'in',  60),
 ('855','855 PO Acknowledgment',     'Order-to-Cash',   'out', 60),
 ('860','860 PO Change',             'Order-to-Cash',   'in',  60),
 ('865','865 PO Change Ack',         'Order-to-Cash',   'out', 60),
 ('856','856 Advance Ship Notice',   'Order-to-Cash',   'out', 120),
 ('810','810 Invoice',               'Procure-to-Pay',  'out', 240),
 ('820','820 Payment/Remittance',    'Procure-to-Pay',  'in',  240),
 -- Transportation
 ('204','204 Load Tender',           'Transportation',  'in',  30),
 ('990','990 Tender Response',       'Transportation',  'out', 30),
 ('214','214 Shipment Status',       'Transportation',  'in',  240),
 ('210','210 Freight Invoice',       'Transportation',  'out', 240),
 ('211','211 Bill of Lading',        'Transportation',  'out', 240),
 -- Warehouse
 ('940','940 Warehouse Ship Order',  'Warehouse',       'out', 120),
 ('945','945 Warehouse Ship Advice', 'Warehouse',       'in',  120),
 ('943','943 Warehouse Stock Xfer',  'Warehouse',       'out', 120),
 ('944','944 Stock Xfer Receipt',    'Warehouse',       'in',  120),
 -- Functional acks
 ('997','997 Functional Ack',        'Functional',      'out', 15),
 ('CONTRL','CONTRL (EDIFACT Ack)',   'Functional',      'out', 15),
 -- Air / customs
 ('HAWB','HAWB Air Waybill',         'Air',             'in',  240)
ON CONFLICT (doc_type) DO UPDATE
  SET label=EXCLUDED.label, business_family=EXCLUDED.business_family,
      typical_direction=EXCLUDED.typical_direction, sla_minutes=EXCLUDED.sla_minutes;
