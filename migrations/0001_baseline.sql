--
-- PostgreSQL database dump
--

-- \restrict pgOt2wqr4z6ahhhqlmW4ygj4MvzhO9NnNGxHXNP9ncyD2fI2ep97FQylVig3udH

-- Dumped from database version 18.4 (Debian 18.4-1.pgdg13+1)
-- Dumped by pg_dump version 18.4 (Debian 18.4-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: bond_analytics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_analytics (
    isin text NOT NULL,
    issuer_name text,
    sector text,
    industry text,
    on_nse boolean NOT NULL,
    on_bse boolean NOT NULL,
    nse_symbol text,
    nse_series text,
    bse_scrip_id text,
    coupon double precision,
    face_value double precision,
    coupon_basis text,
    frequency text,
    secured boolean,
    tax_free boolean,
    perpetual boolean NOT NULL,
    maturity_date date,
    schedule_shape text NOT NULL,
    ltp double precision,
    trade_date date,
    exchange text,
    volume double precision,
    turnover double precision,
    num_trades integer,
    otc_price_pct double precision,
    otc_yield double precision,
    otc_trade_date date,
    ytm double precision,
    ytm_method text,
    current_yield double precision,
    duration double precision,
    credit_rating text,
    rating_rank integer,
    rating_change text,
    rating_prev text,
    rating_changed_at date,
    in_default boolean NOT NULL,
    computed_at timestamp with time zone NOT NULL
);


--
-- Name: bond_arrangers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_arrangers (
    id bigint NOT NULL,
    isin text NOT NULL,
    arranger text,
    contact_person text,
    contact text,
    email text,
    website text,
    address text
);


--
-- Name: bond_arrangers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_arrangers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_arrangers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_arrangers_id_seq OWNED BY public.bond_arrangers.id;


--
-- Name: bond_cashflows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_cashflows (
    id bigint NOT NULL,
    isin text NOT NULL,
    event text,
    due_date date,
    record_date date,
    payment_date date,
    amount_payable numeric,
    amount_redeem numeric,
    amount_outstanding numeric,
    actual_record_date date,
    actual_payment_date date,
    reason_delay_payment text
);


--
-- Name: bond_cashflows_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_cashflows_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_cashflows_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_cashflows_id_seq OWNED BY public.bond_cashflows.id;


--
-- Name: bond_contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_contacts (
    isin text NOT NULL,
    registrar text,
    registrar_contact text,
    registrar_contact_person text,
    registrar_email text,
    registrar_address text,
    registrar_website text,
    trustee_name text,
    trustee_contact text,
    trustee_email text,
    trustee_address text,
    trustee_website text,
    lead_manager text,
    lead_contact text,
    lead_email text,
    lead_address text,
    lead_website text,
    issuer_reg_address text,
    issuer_contact_name text,
    issuer_contact_email text
);


--
-- Name: bond_defaults; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_defaults (
    id bigint NOT NULL,
    isin text NOT NULL,
    sr_no integer,
    issue_nature text,
    issue_size numeric,
    interest_due_date date,
    redemption_due_date date,
    default_details text,
    actual_payment_details text,
    verification_date date,
    verification_status text
);


--
-- Name: bond_defaults_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_defaults_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_defaults_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_defaults_id_seq OWNED BY public.bond_defaults.id;


--
-- Name: bond_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_documents (
    id bigint NOT NULL,
    isin text NOT NULL,
    doc_name text,
    source text,
    download_link text,
    file_id text
);


--
-- Name: bond_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_documents_id_seq OWNED BY public.bond_documents.id;


--
-- Name: bond_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_events (
    id bigint NOT NULL,
    event_date date NOT NULL,
    isin text NOT NULL,
    event_type text NOT NULL,
    field text,
    old_value text,
    new_value text,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT chk_bond_events_type CHECK ((event_type = ANY (ARRAY['added'::text, 'dropped'::text, 'relisted'::text, 'status_change'::text, 'source_change'::text, 'identifier_change'::text])))
);


--
-- Name: bond_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_events_id_seq OWNED BY public.bond_events.id;


--
-- Name: bond_further_issues; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_further_issues (
    id bigint NOT NULL,
    isin text NOT NULL,
    allotment_date date,
    allotment_quantity numeric,
    issue_price numeric,
    issue_size numeric,
    cumulative_quantity numeric,
    cumulative_issue_size numeric
);


--
-- Name: bond_further_issues_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_further_issues_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_further_issues_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_further_issues_id_seq OWNED BY public.bond_further_issues.id;


--
-- Name: bond_holdings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_holdings (
    id bigint NOT NULL,
    isin text NOT NULL,
    quarter text NOT NULL,
    report_date date,
    header text,
    subheader text,
    holder_count integer,
    quantity numeric
);


--
-- Name: bond_holdings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_holdings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_holdings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_holdings_id_seq OWNED BY public.bond_holdings.id;


--
-- Name: bond_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_info (
    isin text NOT NULL,
    issuer_name text,
    cin text,
    lei text,
    macro text,
    sector text,
    industry text,
    basic_industry text,
    sec_type text,
    sec_type_desc text,
    instrument_type text,
    issuer_type_owner text,
    issuer_type_nature text,
    former_name_one text,
    former_name_two text,
    former_name_three text,
    restructured_status text,
    corporate_action_event text,
    new_isin text
);


--
-- Name: bond_listings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_listings (
    id bigint NOT NULL,
    isin text NOT NULL,
    exchange text,
    issue_type text,
    listing_date date,
    allotment_date date,
    quantity numeric,
    term_sheet_link text,
    notification_link text,
    is_current boolean NOT NULL
);


--
-- Name: bond_listings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_listings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_listings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_listings_id_seq OWNED BY public.bond_listings.id;


--
-- Name: bond_ratings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_ratings (
    id bigint NOT NULL,
    isin text NOT NULL,
    agency text,
    agency_name text,
    rating text,
    outlook text,
    rating_action text,
    rating_date date,
    rating_change_date date,
    watch text,
    verification_status text,
    press_release_link text,
    is_current boolean NOT NULL
);


--
-- Name: bond_ratings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_ratings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_ratings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_ratings_id_seq OWNED BY public.bond_ratings.id;


--
-- Name: bond_redemption_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_redemption_events (
    id bigint NOT NULL,
    isin text NOT NULL,
    redemption_date date,
    value_redeemed numeric,
    quantity_redeemed numeric,
    method text,
    reason text
);


--
-- Name: bond_redemption_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_redemption_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_redemption_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_redemption_events_id_seq OWNED BY public.bond_redemption_events.id;


--
-- Name: bond_restructurings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_restructurings (
    isin text NOT NULL,
    event text,
    parent_isin text,
    sec_type text,
    category text,
    coupon_rate text,
    coupon_basis text,
    allotment_date date,
    redemption_date date,
    interest_payment_frequency text,
    parent_sec_type text,
    parent_category text,
    parent_coupon_rate text,
    parent_coupon_basis text,
    parent_allotment_date date,
    parent_redemption_date date,
    parent_interest_payment_frequency text
);


--
-- Name: bond_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_runs (
    id bigint NOT NULL,
    run_at timestamp with time zone NOT NULL,
    nse_corp integer,
    bse_active integer,
    bse_suspended integer,
    overlap integer,
    total integer,
    added integer,
    dropped integer,
    relisted integer,
    ok boolean,
    notes text,
    log text
);


--
-- Name: bond_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_runs_id_seq OWNED BY public.bond_runs.id;


--
-- Name: bond_technical_defaults; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_technical_defaults (
    id bigint NOT NULL,
    isin text NOT NULL,
    agency text,
    payment_due_date date,
    amount_to_be_paid numeric,
    amount_paid numeric,
    amount_payment_failed numeric,
    failure_reason text,
    update_date date,
    press_release_link text
);


--
-- Name: bond_technical_defaults_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bond_technical_defaults_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bond_technical_defaults_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bond_technical_defaults_id_seq OWNED BY public.bond_technical_defaults.id;


--
-- Name: bond_terms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bond_terms (
    isin text NOT NULL,
    series text,
    category text,
    secured text,
    secured_details text,
    tax_free text,
    perpetual boolean,
    face_value numeric,
    issue_price numeric,
    total_issue_size numeric,
    amount_raised numeric,
    allotment_date date,
    redemption_date date,
    tenure_years integer,
    tenure_months integer,
    tenure_days integer,
    mode_of_issue text,
    instrument_desc text,
    nature_of_instrument text,
    green_shoe text,
    asset_coverage text,
    asset_cover_percent numeric,
    coupon_rate numeric,
    coupon_type text,
    coupon_basis text,
    interest_payment_frequency text,
    frequency_description text,
    day_count_convention text,
    business_day_convention text,
    coupon_payout_formula text,
    coupon_reset_process text,
    redemption_type text,
    maturity_type text,
    net_quantity numeric,
    defaulted_redemption text,
    early_redemption_details text,
    redemption_premium_details text,
    put_indicator text,
    put_specified_dates text,
    put_deadline text,
    call_indicator text,
    call_specified_dates text,
    call_deadline text
);


--
-- Name: bonds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bonds (
    isin text NOT NULL,
    status text DEFAULT 'listed'::text NOT NULL,
    on_nse boolean DEFAULT false NOT NULL,
    on_bse boolean DEFAULT false NOT NULL,
    bse_status text,
    issuer_name text,
    nse_symbol text,
    nse_series text,
    bse_scrip_code text,
    bse_scrip_id text,
    created_at timestamp with time zone NOT NULL,
    first_seen timestamp with time zone NOT NULL,
    last_seen timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT chk_bonds_status CHECK ((status = ANY (ARRAY['listed'::text, 'dropped'::text])))
);


--
-- Name: eod_raw; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eod_raw (
    id bigint NOT NULL,
    source text NOT NULL,
    trade_date date NOT NULL,
    run_id bigint,
    fetched_at timestamp with time zone NOT NULL,
    url text,
    http_status integer NOT NULL,
    sha256 text,
    content bytea,
    parsed_at timestamp with time zone,
    CONSTRAINT chk_eod_raw_source CHECK ((source = ANY (ARRAY['nse_cm'::text, 'nse_cbm'::text, 'bse_debt'::text])))
);


--
-- Name: eod_raw_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.eod_raw_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: eod_raw_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.eod_raw_id_seq OWNED BY public.eod_raw.id;


--
-- Name: eod_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eod_runs (
    id bigint NOT NULL,
    run_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    date_from date,
    date_to date,
    fetched integer,
    absent integer,
    skipped integer,
    failed integer,
    ok boolean,
    notes text
);


--
-- Name: eod_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.eod_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: eod_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.eod_runs_id_seq OWNED BY public.eod_runs.id;


--
-- Name: eod_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eod_trades (
    isin text NOT NULL,
    trade_date date NOT NULL,
    exchange text NOT NULL,
    segment text NOT NULL,
    price_basis text NOT NULL,
    open numeric,
    high numeric,
    low numeric,
    close numeric,
    prev_close numeric,
    last_price numeric,
    wap numeric,
    last_yield numeric,
    wap_yield numeric,
    volume numeric,
    turnover numeric,
    num_trades integer,
    raw_id bigint,
    CONSTRAINT chk_trades_exchange CHECK ((exchange = ANY (ARRAY['NSE'::text, 'BSE'::text]))),
    CONSTRAINT chk_trades_price_basis CHECK ((price_basis = ANY (ARRAY['dirty_rupees'::text, 'clean_pct'::text]))),
    CONSTRAINT chk_trades_segment CHECK ((segment = ANY (ARRAY['retail'::text, 'otc'::text])))
);


--
-- Name: nsdl_raw; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nsdl_raw (
    id bigint NOT NULL,
    isin text NOT NULL,
    endpoint text NOT NULL,
    quarter text DEFAULT ''::text NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    last_checked_at timestamp with time zone NOT NULL,
    http_status integer NOT NULL,
    sha256 text,
    payload jsonb,
    run_id bigint
);


--
-- Name: nsdl_raw_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nsdl_raw_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nsdl_raw_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nsdl_raw_id_seq OWNED BY public.nsdl_raw.id;


--
-- Name: nsdl_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nsdl_runs (
    id bigint NOT NULL,
    run_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    tiers text,
    workers integer,
    isins integer,
    fetched integer,
    unchanged integer,
    skipped integer,
    failed integer,
    ok boolean,
    notes text
);


--
-- Name: nsdl_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.nsdl_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: nsdl_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.nsdl_runs_id_seq OWNED BY public.nsdl_runs.id;


--
-- Name: raw_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_files (
    id bigint NOT NULL,
    run_id bigint,
    fetched_at timestamp with time zone NOT NULL,
    source text NOT NULL,
    sha256 text NOT NULL,
    size_bytes integer NOT NULL,
    content bytea NOT NULL
);


--
-- Name: raw_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.raw_files_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raw_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.raw_files_id_seq OWNED BY public.raw_files.id;


--
-- Name: watchlist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlist (
    isin text NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: bond_arrangers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_arrangers ALTER COLUMN id SET DEFAULT nextval('public.bond_arrangers_id_seq'::regclass);


--
-- Name: bond_cashflows id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_cashflows ALTER COLUMN id SET DEFAULT nextval('public.bond_cashflows_id_seq'::regclass);


--
-- Name: bond_defaults id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_defaults ALTER COLUMN id SET DEFAULT nextval('public.bond_defaults_id_seq'::regclass);


--
-- Name: bond_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_documents ALTER COLUMN id SET DEFAULT nextval('public.bond_documents_id_seq'::regclass);


--
-- Name: bond_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_events ALTER COLUMN id SET DEFAULT nextval('public.bond_events_id_seq'::regclass);


--
-- Name: bond_further_issues id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_further_issues ALTER COLUMN id SET DEFAULT nextval('public.bond_further_issues_id_seq'::regclass);


--
-- Name: bond_holdings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_holdings ALTER COLUMN id SET DEFAULT nextval('public.bond_holdings_id_seq'::regclass);


--
-- Name: bond_listings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_listings ALTER COLUMN id SET DEFAULT nextval('public.bond_listings_id_seq'::regclass);


--
-- Name: bond_ratings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_ratings ALTER COLUMN id SET DEFAULT nextval('public.bond_ratings_id_seq'::regclass);


--
-- Name: bond_redemption_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_redemption_events ALTER COLUMN id SET DEFAULT nextval('public.bond_redemption_events_id_seq'::regclass);


--
-- Name: bond_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_runs ALTER COLUMN id SET DEFAULT nextval('public.bond_runs_id_seq'::regclass);


--
-- Name: bond_technical_defaults id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_technical_defaults ALTER COLUMN id SET DEFAULT nextval('public.bond_technical_defaults_id_seq'::regclass);


--
-- Name: eod_raw id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_raw ALTER COLUMN id SET DEFAULT nextval('public.eod_raw_id_seq'::regclass);


--
-- Name: eod_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_runs ALTER COLUMN id SET DEFAULT nextval('public.eod_runs_id_seq'::regclass);


--
-- Name: nsdl_raw id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nsdl_raw ALTER COLUMN id SET DEFAULT nextval('public.nsdl_raw_id_seq'::regclass);


--
-- Name: nsdl_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nsdl_runs ALTER COLUMN id SET DEFAULT nextval('public.nsdl_runs_id_seq'::regclass);


--
-- Name: raw_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_files ALTER COLUMN id SET DEFAULT nextval('public.raw_files_id_seq'::regclass);


--
-- Name: bond_analytics bond_analytics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_analytics
    ADD CONSTRAINT bond_analytics_pkey PRIMARY KEY (isin);


--
-- Name: bond_arrangers bond_arrangers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_arrangers
    ADD CONSTRAINT bond_arrangers_pkey PRIMARY KEY (id);


--
-- Name: bond_cashflows bond_cashflows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_cashflows
    ADD CONSTRAINT bond_cashflows_pkey PRIMARY KEY (id);


--
-- Name: bond_contacts bond_contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_contacts
    ADD CONSTRAINT bond_contacts_pkey PRIMARY KEY (isin);


--
-- Name: bond_defaults bond_defaults_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_defaults
    ADD CONSTRAINT bond_defaults_pkey PRIMARY KEY (id);


--
-- Name: bond_documents bond_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_documents
    ADD CONSTRAINT bond_documents_pkey PRIMARY KEY (id);


--
-- Name: bond_events bond_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_events
    ADD CONSTRAINT bond_events_pkey PRIMARY KEY (id);


--
-- Name: bond_further_issues bond_further_issues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_further_issues
    ADD CONSTRAINT bond_further_issues_pkey PRIMARY KEY (id);


--
-- Name: bond_holdings bond_holdings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_holdings
    ADD CONSTRAINT bond_holdings_pkey PRIMARY KEY (id);


--
-- Name: bond_info bond_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_info
    ADD CONSTRAINT bond_info_pkey PRIMARY KEY (isin);


--
-- Name: bond_listings bond_listings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_listings
    ADD CONSTRAINT bond_listings_pkey PRIMARY KEY (id);


--
-- Name: bond_ratings bond_ratings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_ratings
    ADD CONSTRAINT bond_ratings_pkey PRIMARY KEY (id);


--
-- Name: bond_redemption_events bond_redemption_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_redemption_events
    ADD CONSTRAINT bond_redemption_events_pkey PRIMARY KEY (id);


--
-- Name: bond_restructurings bond_restructurings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_restructurings
    ADD CONSTRAINT bond_restructurings_pkey PRIMARY KEY (isin);


--
-- Name: bond_runs bond_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_runs
    ADD CONSTRAINT bond_runs_pkey PRIMARY KEY (id);


--
-- Name: bond_technical_defaults bond_technical_defaults_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_technical_defaults
    ADD CONSTRAINT bond_technical_defaults_pkey PRIMARY KEY (id);


--
-- Name: bond_terms bond_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_terms
    ADD CONSTRAINT bond_terms_pkey PRIMARY KEY (isin);


--
-- Name: bonds bonds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bonds
    ADD CONSTRAINT bonds_pkey PRIMARY KEY (isin);


--
-- Name: eod_raw eod_raw_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_raw
    ADD CONSTRAINT eod_raw_pkey PRIMARY KEY (id);


--
-- Name: eod_raw eod_raw_source_trade_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_raw
    ADD CONSTRAINT eod_raw_source_trade_date_key UNIQUE (source, trade_date);


--
-- Name: eod_runs eod_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_runs
    ADD CONSTRAINT eod_runs_pkey PRIMARY KEY (id);


--
-- Name: nsdl_raw nsdl_raw_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nsdl_raw
    ADD CONSTRAINT nsdl_raw_pkey PRIMARY KEY (id);


--
-- Name: nsdl_runs nsdl_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nsdl_runs
    ADD CONSTRAINT nsdl_runs_pkey PRIMARY KEY (id);


--
-- Name: raw_files raw_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_files
    ADD CONSTRAINT raw_files_pkey PRIMARY KEY (id);


--
-- Name: raw_files raw_files_source_sha256_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_files
    ADD CONSTRAINT raw_files_source_sha256_key UNIQUE (source, sha256);


--
-- Name: eod_trades trades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_trades
    ADD CONSTRAINT trades_pkey PRIMARY KEY (isin, trade_date, exchange, segment);


--
-- Name: watchlist watchlist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_pkey PRIMARY KEY (isin);


--
-- Name: idx_analytics_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_rank ON public.bond_analytics USING btree (rating_rank);


--
-- Name: idx_analytics_turnover; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_turnover ON public.bond_analytics USING btree (turnover DESC NULLS LAST);


--
-- Name: idx_analytics_ytm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_ytm ON public.bond_analytics USING btree (ytm);


--
-- Name: idx_bond_cashflows_isin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bond_cashflows_isin ON public.bond_cashflows USING btree (isin, due_date);


--
-- Name: idx_bond_events_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bond_events_date ON public.bond_events USING btree (event_date);


--
-- Name: idx_bond_events_isin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bond_events_isin ON public.bond_events USING btree (isin);


--
-- Name: idx_bond_holdings_isin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bond_holdings_isin ON public.bond_holdings USING btree (isin, quarter);


--
-- Name: idx_bond_ratings_isin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bond_ratings_isin ON public.bond_ratings USING btree (isin, is_current, rating_date);


--
-- Name: idx_bonds_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bonds_status ON public.bonds USING btree (status);


--
-- Name: idx_eod_raw_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eod_raw_key ON public.eod_raw USING btree (source, trade_date);


--
-- Name: idx_eod_trades_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_eod_trades_date ON public.eod_trades USING btree (trade_date);


--
-- Name: idx_nsdl_raw_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_nsdl_raw_key ON public.nsdl_raw USING btree (isin, endpoint, quarter, fetched_at DESC);


--
-- Name: bond_analytics bond_analytics_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_analytics
    ADD CONSTRAINT bond_analytics_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_arrangers bond_arrangers_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_arrangers
    ADD CONSTRAINT bond_arrangers_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_cashflows bond_cashflows_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_cashflows
    ADD CONSTRAINT bond_cashflows_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_contacts bond_contacts_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_contacts
    ADD CONSTRAINT bond_contacts_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_defaults bond_defaults_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_defaults
    ADD CONSTRAINT bond_defaults_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_documents bond_documents_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_documents
    ADD CONSTRAINT bond_documents_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_further_issues bond_further_issues_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_further_issues
    ADD CONSTRAINT bond_further_issues_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_holdings bond_holdings_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_holdings
    ADD CONSTRAINT bond_holdings_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_info bond_info_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_info
    ADD CONSTRAINT bond_info_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_listings bond_listings_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_listings
    ADD CONSTRAINT bond_listings_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_ratings bond_ratings_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_ratings
    ADD CONSTRAINT bond_ratings_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_redemption_events bond_redemption_events_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_redemption_events
    ADD CONSTRAINT bond_redemption_events_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_restructurings bond_restructurings_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_restructurings
    ADD CONSTRAINT bond_restructurings_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_technical_defaults bond_technical_defaults_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_technical_defaults
    ADD CONSTRAINT bond_technical_defaults_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: bond_terms bond_terms_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bond_terms
    ADD CONSTRAINT bond_terms_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: eod_raw eod_raw_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_raw
    ADD CONSTRAINT eod_raw_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.eod_runs(id);


--
-- Name: nsdl_raw nsdl_raw_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nsdl_raw
    ADD CONSTRAINT nsdl_raw_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: nsdl_raw nsdl_raw_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nsdl_raw
    ADD CONSTRAINT nsdl_raw_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.nsdl_runs(id);


--
-- Name: raw_files raw_files_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_files
    ADD CONSTRAINT raw_files_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.bond_runs(id);


--
-- Name: eod_trades trades_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eod_trades
    ADD CONSTRAINT trades_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- Name: watchlist watchlist_isin_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlist
    ADD CONSTRAINT watchlist_isin_fkey FOREIGN KEY (isin) REFERENCES public.bonds(isin);


--
-- PostgreSQL database dump complete
--

-- \unrestrict pgOt2wqr4z6ahhhqlmW4ygj4MvzhO9NnNGxHXNP9ncyD2fI2ep97FQylVig3udH

