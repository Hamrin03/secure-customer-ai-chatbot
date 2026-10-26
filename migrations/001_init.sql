CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE customers (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  mobile TEXT NOT NULL UNIQUE CHECK (mobile ~ '^\+91[6-9][0-9]{9}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE invoices (
  id BIGSERIAL PRIMARY KEY,
  customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  invoice_number TEXT NOT NULL,
  invoice_date DATE NOT NULL,
  amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  currency TEXT NOT NULL CHECK (char_length(currency) = 3),
  status TEXT NOT NULL CHECK (status IN ('paid','unpaid')),
  indexed BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (customer_id, invoice_number),
  UNIQUE (id, customer_id)
);

CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id, customer_id)
);

CREATE TABLE messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id UUID NOT NULL,
  customer_id BIGINT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (conversation_id, customer_id)
    REFERENCES conversations(id, customer_id) ON DELETE CASCADE
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, id);

CREATE TABLE otp_requests (
  id BIGSERIAL PRIMARY KEY,
  mobile TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  attempts INT NOT NULL DEFAULT 0,
  used BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX idx_otp_mobile ON otp_requests(mobile, created_at DESC);

CREATE TABLE access_tokens (
  token_hash TEXT PRIMARY KEY,
  customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE invoice_embeddings (
  invoice_id BIGINT PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  content TEXT NOT NULL,
  embedding vector(768) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (invoice_id, customer_id) REFERENCES invoices(id, customer_id) ON DELETE CASCADE
);
CREATE INDEX idx_emb_customer ON invoice_embeddings(customer_id);

CREATE TABLE tool_calls (
  id BIGSERIAL PRIMARY KEY,
  conversation_id UUID NOT NULL,
  customer_id BIGINT NOT NULL,
  tool_name TEXT NOT NULL,
  arguments JSONB,
  outcome TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (conversation_id, customer_id) REFERENCES conversations(id, customer_id) ON DELETE CASCADE
);

CREATE TABLE pending_actions (
  conversation_id UUID PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (conversation_id, customer_id) REFERENCES conversations(id, customer_id) ON DELETE CASCADE
);
