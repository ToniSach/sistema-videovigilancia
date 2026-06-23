--
-- PostgreSQL database dump
--

-- Dumped from database version 17.5
-- Dumped by pg_dump version 17.5

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
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id integer NOT NULL,
    user_id integer,
    action character varying(100) NOT NULL,
    resource_type character varying(50),
    resource_id character varying(100),
    details text,
    ip_address character varying(45),
    user_agent character varying(500),
    created_at timestamp without time zone NOT NULL
);


--
-- Name: audit_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_logs_id_seq OWNED BY public.audit_logs.id;


--
-- Name: cameras; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cameras (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    ip_address character varying(45) NOT NULL,
    rtsp_url character varying(500) NOT NULL,
    onvif_url character varying(500),
    username character varying(100),
    password character varying(100),
    profile_token character varying(100),
    is_active boolean NOT NULL,
    has_ai boolean NOT NULL,
    has_ptz boolean NOT NULL,
    has_leds boolean NOT NULL,
    has_audio boolean NOT NULL,
    is_dual_lens boolean NOT NULL,
    resolution_width integer NOT NULL,
    resolution_height integer NOT NULL,
    fps integer NOT NULL,
    created_at timestamp without time zone NOT NULL,
    connection_type character varying(20) NOT NULL,
    last_error_code character varying(50),
    last_connected_at timestamp without time zone,
    fallback_url character varying(500),
    owner_id integer
);


--
-- Name: cameras_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cameras_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cameras_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cameras_id_seq OWNED BY public.cameras.id;


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id integer NOT NULL,
    camera_id integer NOT NULL,
    event_type character varying(50) NOT NULL,
    confidence double precision NOT NULL,
    snapshot_path character varying(500),
    clip_path character varying(500),
    acknowledged boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;


--
-- Name: link_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.link_tokens (
    id integer NOT NULL,
    token character varying(36) NOT NULL,
    user_id integer NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    used boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: link_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.link_tokens_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: link_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.link_tokens_id_seq OWNED BY public.link_tokens.id;


--
-- Name: mobile_devices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mobile_devices (
    id integer NOT NULL,
    user_id integer NOT NULL,
    device_uuid character varying(100) NOT NULL,
    device_name character varying(100) NOT NULL,
    platform character varying(20) NOT NULL,
    is_active boolean NOT NULL,
    last_seen_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL,
    refresh_token_hash character varying(255) NOT NULL
);


--
-- Name: mobile_devices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mobile_devices_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mobile_devices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mobile_devices_id_seq OWNED BY public.mobile_devices.id;


--
-- Name: notification_channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_channels (
    id integer NOT NULL,
    preference_id integer NOT NULL,
    channel character varying(20) NOT NULL
);


--
-- Name: notification_channels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notification_channels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notification_channels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notification_channels_id_seq OWNED BY public.notification_channels.id;


--
-- Name: notification_days; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_days (
    id integer NOT NULL,
    preference_id integer NOT NULL,
    day_of_week integer NOT NULL
);


--
-- Name: notification_days_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notification_days_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notification_days_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notification_days_id_seq OWNED BY public.notification_days.id;


--
-- Name: notification_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_logs (
    id integer NOT NULL,
    event_id integer NOT NULL,
    user_id integer NOT NULL,
    channel character varying(20) NOT NULL,
    status character varying(20) NOT NULL,
    error_message text,
    sent_at timestamp without time zone NOT NULL,
    cooldown_key character varying(200)
);


--
-- Name: notification_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notification_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notification_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notification_logs_id_seq OWNED BY public.notification_logs.id;


--
-- Name: notification_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_preferences (
    id integer NOT NULL,
    user_id integer NOT NULL,
    device_id integer,
    event_type character varying(50) NOT NULL,
    camera_id integer,
    enabled boolean NOT NULL,
    schedule_start time without time zone,
    schedule_end time without time zone,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: notification_preferences_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notification_preferences_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notification_preferences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notification_preferences_id_seq OWNED BY public.notification_preferences.id;


--
-- Name: recordings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recordings (
    id integer NOT NULL,
    camera_id integer NOT NULL,
    start_time timestamp without time zone NOT NULL,
    end_time timestamp without time zone,
    file_path character varying(500) NOT NULL,
    file_size_bytes integer NOT NULL,
    duration_seconds double precision NOT NULL,
    is_favorite boolean NOT NULL
);


--
-- Name: recordings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.recordings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: recordings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.recordings_id_seq OWNED BY public.recordings.id;


--
-- Name: revoked_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.revoked_tokens (
    id integer NOT NULL,
    jti character varying(64) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    revoked_at timestamp without time zone NOT NULL,
    user_id integer,
    reason character varying(50)
);


--
-- Name: revoked_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.revoked_tokens_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: revoked_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.revoked_tokens_id_seq OWNED BY public.revoked_tokens.id;


--
-- Name: system_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_config (
    id integer NOT NULL,
    key character varying(100) NOT NULL,
    value text NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: system_config_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.system_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: system_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.system_config_id_seq OWNED BY public.system_config.id;


--
-- Name: telegram_verification_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_verification_codes (
    id integer NOT NULL,
    user_id integer NOT NULL,
    device_id integer,
    code character varying(10) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    used boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: telegram_verification_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.telegram_verification_codes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: telegram_verification_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.telegram_verification_codes_id_seq OWNED BY public.telegram_verification_codes.id;


--
-- Name: user_camera_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_camera_permissions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    camera_id integer NOT NULL,
    can_view boolean NOT NULL,
    can_control_ptz boolean NOT NULL,
    can_control_leds boolean NOT NULL,
    can_control_audio boolean NOT NULL,
    can_download_recordings boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: user_camera_permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_camera_permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_camera_permissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_camera_permissions_id_seq OWNED BY public.user_camera_permissions.id;


--
-- Name: user_telegram_chats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_telegram_chats (
    id integer NOT NULL,
    user_id integer NOT NULL,
    device_id integer,
    telegram_chat_id character varying(50) NOT NULL,
    telegram_username character varying(100),
    linked_at timestamp without time zone NOT NULL,
    is_active boolean NOT NULL
);


--
-- Name: user_telegram_chats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_telegram_chats_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_telegram_chats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_telegram_chats_id_seq OWNED BY public.user_telegram_chats.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(20) NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: audit_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs ALTER COLUMN id SET DEFAULT nextval('public.audit_logs_id_seq'::regclass);


--
-- Name: cameras id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cameras ALTER COLUMN id SET DEFAULT nextval('public.cameras_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);


--
-- Name: link_tokens id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_tokens ALTER COLUMN id SET DEFAULT nextval('public.link_tokens_id_seq'::regclass);


--
-- Name: mobile_devices id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mobile_devices ALTER COLUMN id SET DEFAULT nextval('public.mobile_devices_id_seq'::regclass);


--
-- Name: notification_channels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_channels ALTER COLUMN id SET DEFAULT nextval('public.notification_channels_id_seq'::regclass);


--
-- Name: notification_days id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_days ALTER COLUMN id SET DEFAULT nextval('public.notification_days_id_seq'::regclass);


--
-- Name: notification_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_logs ALTER COLUMN id SET DEFAULT nextval('public.notification_logs_id_seq'::regclass);


--
-- Name: notification_preferences id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_preferences ALTER COLUMN id SET DEFAULT nextval('public.notification_preferences_id_seq'::regclass);


--
-- Name: recordings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings ALTER COLUMN id SET DEFAULT nextval('public.recordings_id_seq'::regclass);


--
-- Name: revoked_tokens id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.revoked_tokens ALTER COLUMN id SET DEFAULT nextval('public.revoked_tokens_id_seq'::regclass);


--
-- Name: system_config id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_config ALTER COLUMN id SET DEFAULT nextval('public.system_config_id_seq'::regclass);


--
-- Name: telegram_verification_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_verification_codes ALTER COLUMN id SET DEFAULT nextval('public.telegram_verification_codes_id_seq'::regclass);


--
-- Name: user_camera_permissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_camera_permissions ALTER COLUMN id SET DEFAULT nextval('public.user_camera_permissions_id_seq'::regclass);


--
-- Name: user_telegram_chats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_telegram_chats ALTER COLUMN id SET DEFAULT nextval('public.user_telegram_chats_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: cameras cameras_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cameras
    ADD CONSTRAINT cameras_pkey PRIMARY KEY (id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: link_tokens link_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_tokens
    ADD CONSTRAINT link_tokens_pkey PRIMARY KEY (id);


--
-- Name: mobile_devices mobile_devices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mobile_devices
    ADD CONSTRAINT mobile_devices_pkey PRIMARY KEY (id);


--
-- Name: notification_channels notification_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_channels
    ADD CONSTRAINT notification_channels_pkey PRIMARY KEY (id);


--
-- Name: notification_days notification_days_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_days
    ADD CONSTRAINT notification_days_pkey PRIMARY KEY (id);


--
-- Name: notification_logs notification_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_logs
    ADD CONSTRAINT notification_logs_pkey PRIMARY KEY (id);


--
-- Name: notification_preferences notification_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_preferences
    ADD CONSTRAINT notification_preferences_pkey PRIMARY KEY (id);


--
-- Name: recordings recordings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT recordings_pkey PRIMARY KEY (id);


--
-- Name: revoked_tokens revoked_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.revoked_tokens
    ADD CONSTRAINT revoked_tokens_pkey PRIMARY KEY (id);


--
-- Name: system_config system_config_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT system_config_key_key UNIQUE (key);


--
-- Name: system_config system_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT system_config_pkey PRIMARY KEY (id);


--
-- Name: telegram_verification_codes telegram_verification_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_verification_codes
    ADD CONSTRAINT telegram_verification_codes_pkey PRIMARY KEY (id);


--
-- Name: user_camera_permissions uq_user_camera; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_camera_permissions
    ADD CONSTRAINT uq_user_camera UNIQUE (user_id, camera_id);


--
-- Name: notification_preferences uq_user_device_event_camera; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_preferences
    ADD CONSTRAINT uq_user_device_event_camera UNIQUE (user_id, device_id, event_type, camera_id);


--
-- Name: user_camera_permissions user_camera_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_camera_permissions
    ADD CONSTRAINT user_camera_permissions_pkey PRIMARY KEY (id);


--
-- Name: user_telegram_chats user_telegram_chats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_telegram_chats
    ADD CONSTRAINT user_telegram_chats_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: idx_audit_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_action ON public.audit_logs USING btree (action);


--
-- Name: idx_audit_user_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_user_time ON public.audit_logs USING btree (user_id, created_at);


--
-- Name: idx_event_acknowledged; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_acknowledged ON public.events USING btree (acknowledged, created_at);


--
-- Name: idx_event_camera_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_camera_created ON public.events USING btree (camera_id, created_at);


--
-- Name: idx_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_type ON public.events USING btree (event_type);


--
-- Name: idx_link_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_link_token ON public.link_tokens USING btree (token);


--
-- Name: idx_link_token_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_link_token_user ON public.link_tokens USING btree (user_id);


--
-- Name: idx_mobile_device_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mobile_device_user ON public.mobile_devices USING btree (user_id);


--
-- Name: idx_mobile_device_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mobile_device_uuid ON public.mobile_devices USING btree (device_uuid);


--
-- Name: idx_notification_cooldown; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notification_cooldown ON public.notification_logs USING btree (cooldown_key);


--
-- Name: idx_notification_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notification_event ON public.notification_logs USING btree (event_id);


--
-- Name: idx_notification_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notification_user ON public.notification_logs USING btree (user_id);


--
-- Name: idx_recording_camera_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recording_camera_time ON public.recordings USING btree (camera_id, start_time);


--
-- Name: idx_telegram_chat_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_telegram_chat_id ON public.user_telegram_chats USING btree (telegram_chat_id);


--
-- Name: idx_telegram_chat_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_telegram_chat_user ON public.user_telegram_chats USING btree (user_id);


--
-- Name: idx_telegram_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_telegram_code ON public.telegram_verification_codes USING btree (code);


--
-- Name: idx_telegram_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_telegram_user ON public.telegram_verification_codes USING btree (user_id);


--
-- Name: idx_user_camera_perms; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_camera_perms ON public.user_camera_permissions USING btree (user_id, camera_id);


--
-- Name: ix_audit_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_created_at ON public.audit_logs USING btree (created_at);


--
-- Name: ix_audit_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_user_id ON public.audit_logs USING btree (user_id);


--
-- Name: ix_cameras_owner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cameras_owner_id ON public.cameras USING btree (owner_id);


--
-- Name: ix_link_tokens_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_tokens_expires_at ON public.link_tokens USING btree (expires_at);


--
-- Name: ix_link_tokens_token; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_link_tokens_token ON public.link_tokens USING btree (token);


--
-- Name: ix_link_tokens_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_link_tokens_user_id ON public.link_tokens USING btree (user_id);


--
-- Name: ix_mobile_devices_device_uuid; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_mobile_devices_device_uuid ON public.mobile_devices USING btree (device_uuid);


--
-- Name: ix_mobile_devices_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mobile_devices_user_id ON public.mobile_devices USING btree (user_id);


--
-- Name: ix_notification_channels_preference_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_channels_preference_id ON public.notification_channels USING btree (preference_id);


--
-- Name: ix_notification_days_preference_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_days_preference_id ON public.notification_days USING btree (preference_id);


--
-- Name: ix_notification_logs_cooldown_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_logs_cooldown_key ON public.notification_logs USING btree (cooldown_key);


--
-- Name: ix_notification_logs_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_logs_event_id ON public.notification_logs USING btree (event_id);


--
-- Name: ix_notification_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_logs_user_id ON public.notification_logs USING btree (user_id);


--
-- Name: ix_notification_preferences_camera_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_preferences_camera_id ON public.notification_preferences USING btree (camera_id);


--
-- Name: ix_notification_preferences_device_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_preferences_device_id ON public.notification_preferences USING btree (device_id);


--
-- Name: ix_notification_preferences_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_preferences_event_type ON public.notification_preferences USING btree (event_type);


--
-- Name: ix_notification_preferences_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_preferences_user_id ON public.notification_preferences USING btree (user_id);


--
-- Name: ix_revoked_tokens_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_revoked_tokens_expires_at ON public.revoked_tokens USING btree (expires_at);


--
-- Name: ix_revoked_tokens_jti; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_revoked_tokens_jti ON public.revoked_tokens USING btree (jti);


--
-- Name: ix_telegram_verification_codes_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_telegram_verification_codes_code ON public.telegram_verification_codes USING btree (code);


--
-- Name: ix_telegram_verification_codes_device_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_verification_codes_device_id ON public.telegram_verification_codes USING btree (device_id);


--
-- Name: ix_telegram_verification_codes_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_verification_codes_expires_at ON public.telegram_verification_codes USING btree (expires_at);


--
-- Name: ix_telegram_verification_codes_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_verification_codes_user_id ON public.telegram_verification_codes USING btree (user_id);


--
-- Name: ix_user_camera_permissions_camera_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_camera_permissions_camera_id ON public.user_camera_permissions USING btree (camera_id);


--
-- Name: ix_user_camera_permissions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_camera_permissions_user_id ON public.user_camera_permissions USING btree (user_id);


--
-- Name: ix_user_telegram_chats_device_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_telegram_chats_device_id ON public.user_telegram_chats USING btree (device_id);


--
-- Name: ix_user_telegram_chats_telegram_chat_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_telegram_chats_telegram_chat_id ON public.user_telegram_chats USING btree (telegram_chat_id);


--
-- Name: ix_user_telegram_chats_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_telegram_chats_user_id ON public.user_telegram_chats USING btree (user_id);


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: cameras cameras_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cameras
    ADD CONSTRAINT cameras_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id);


--
-- Name: events events_camera_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES public.cameras(id) ON DELETE CASCADE;


--
-- Name: link_tokens link_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.link_tokens
    ADD CONSTRAINT link_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: mobile_devices mobile_devices_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mobile_devices
    ADD CONSTRAINT mobile_devices_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: notification_channels notification_channels_preference_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_channels
    ADD CONSTRAINT notification_channels_preference_id_fkey FOREIGN KEY (preference_id) REFERENCES public.notification_preferences(id);


--
-- Name: notification_days notification_days_preference_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_days
    ADD CONSTRAINT notification_days_preference_id_fkey FOREIGN KEY (preference_id) REFERENCES public.notification_preferences(id);


--
-- Name: notification_logs notification_logs_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_logs
    ADD CONSTRAINT notification_logs_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id);


--
-- Name: notification_logs notification_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_logs
    ADD CONSTRAINT notification_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: notification_preferences notification_preferences_camera_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_preferences
    ADD CONSTRAINT notification_preferences_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES public.cameras(id) ON DELETE SET NULL;


--
-- Name: notification_preferences notification_preferences_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_preferences
    ADD CONSTRAINT notification_preferences_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.mobile_devices(id) ON DELETE CASCADE;


--
-- Name: notification_preferences notification_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_preferences
    ADD CONSTRAINT notification_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: recordings recordings_camera_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT recordings_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES public.cameras(id) ON DELETE CASCADE;


--
-- Name: revoked_tokens revoked_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.revoked_tokens
    ADD CONSTRAINT revoked_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: telegram_verification_codes telegram_verification_codes_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_verification_codes
    ADD CONSTRAINT telegram_verification_codes_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.mobile_devices(id) ON DELETE CASCADE;


--
-- Name: telegram_verification_codes telegram_verification_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_verification_codes
    ADD CONSTRAINT telegram_verification_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: user_camera_permissions user_camera_permissions_camera_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_camera_permissions
    ADD CONSTRAINT user_camera_permissions_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES public.cameras(id) ON DELETE CASCADE;


--
-- Name: user_camera_permissions user_camera_permissions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_camera_permissions
    ADD CONSTRAINT user_camera_permissions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_telegram_chats user_telegram_chats_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_telegram_chats
    ADD CONSTRAINT user_telegram_chats_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.mobile_devices(id) ON DELETE CASCADE;


--
-- Name: user_telegram_chats user_telegram_chats_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_telegram_chats
    ADD CONSTRAINT user_telegram_chats_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--

