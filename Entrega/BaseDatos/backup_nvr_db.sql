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
-- Data for Name: audit_logs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.audit_logs (id, user_id, action, resource_type, resource_id, details, ip_address, user_agent, created_at) FROM stdin;
1	1	add_camera	camera	\N	{'success': True}	127.0.0.1	python-requests/2.32.3	2026-06-18 19:37:59.167536
2	1	add_camera	camera	\N	{'success': True}	127.0.0.1	python-requests/2.32.3	2026-06-18 19:38:00.65814
3	1	delete_camera	camera	\N	{'success': True}	127.0.0.1	python-requests/2.32.3	2026-06-18 19:44:37.042672
4	1	delete_camera	camera	\N	{'success': True}	127.0.0.1	python-requests/2.32.3	2026-06-18 19:44:44.954153
5	1	add_camera	camera	\N	{'success': True}	127.0.0.1	python-requests/2.32.3	2026-06-18 20:11:11.828794
6	1	add_camera	camera	\N	{'success': True}	127.0.0.1	python-requests/2.32.3	2026-06-18 20:11:20.427196
7	1	ai_activate	camera	\N	{'success': True}	127.0.0.1	python-requests/2.32.3	2026-06-18 20:12:47.961137
\.


--
-- Data for Name: cameras; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.cameras (id, name, ip_address, rtsp_url, onvif_url, username, password, profile_token, is_active, has_ai, has_ptz, has_leds, has_audio, is_dual_lens, resolution_width, resolution_height, fps, created_at, connection_type, last_error_code, last_connected_at, fallback_url, owner_id) FROM stdin;
4	RTSP Camera (xiongmai)	192.168.0.102	rtsp://admin:admin@192.168.0.102:554/av0_0		admin	admin		t	f	f	f	f	f	1920	1080	15	2026-06-18 20:11:20.383583	rtsp_fallback	\N	\N	\N	1
3	XM535_X6E-WEQ_8M	192.168.0.100	rtsp://admin:admin@192.168.0.100:554/user=admin_password=tlJwpbo6_channel=0_stream=0&onvif=0.sdp?real_stream	http://192.168.0.100:8899/onvif/device_service	admin	admin	000	t	t	t	t	t	t	3072	2048	12	2026-06-18 20:11:11.768575	onvif	\N	\N	\N	1
\.


--
-- Data for Name: events; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.events (id, camera_id, event_type, confidence, snapshot_path, clip_path, acknowledged, created_at) FROM stdin;
1	3	motion	0.3	C:\\nvr_data\\recordings\\snapshots\\3\\1781813585_motion.jpg	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_201305.mp4	f	2026-06-18 20:13:05.379572
2	3	person	0.679387092590332	C:\\nvr_data\\recordings\\snapshots\\3\\1781813627_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201347.mp4	f	2026-06-18 20:13:47.743279
3	3	motion	0.3	C:\\nvr_data\\recordings\\snapshots\\3\\1781813714_motion.jpg	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_201514.mp4	f	2026-06-18 20:15:14.778089
4	3	person	0.5775037407875061	C:\\nvr_data\\recordings\\snapshots\\3\\1781813758_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201558.mp4	f	2026-06-18 20:15:59.034674
5	3	motion	0.38177897135416666	C:\\nvr_data\\recordings\\snapshots\\3\\1781813835_motion.jpg	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_201715.mp4	f	2026-06-18 20:17:15.882313
6	3	person	0.5470186471939087	C:\\nvr_data\\recordings\\snapshots\\3\\1781813912_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201832.mp4	f	2026-06-18 20:18:32.401971
7	3	person	0.6422857642173767	C:\\nvr_data\\recordings\\snapshots\\3\\1781813964_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201924.mp4	f	2026-06-18 20:19:24.423405
8	3	person	0.4022231101989746	C:\\nvr_data\\recordings\\snapshots\\3\\1781813997_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201957.mp4	f	2026-06-18 20:19:57.938529
9	3	person	0.4674415588378906	C:\\nvr_data\\recordings\\snapshots\\3\\1781814143_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_202223.mp4	f	2026-06-18 20:22:23.94193
10	3	person	0.3901851177215576	C:\\nvr_data\\recordings\\snapshots\\3\\1781814221_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_202341.mp4	f	2026-06-18 20:23:41.405507
11	3	person	0.42171788215637207	C:\\nvr_data\\recordings\\snapshots\\3\\1781814920_person.jpg	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_203520.mp4	f	2026-06-18 20:35:20.968232
12	3	motion	0.3	C:\\nvr_data\\recordings\\snapshots\\3\\1781815135_motion.jpg	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_203855.mp4	f	2026-06-18 20:38:55.141727
13	3	person	0.4010215997695923	C:\\nvr_data\\recordings\\snapshots\\3\\1781815241_person.jpg	\N	f	2026-06-18 20:40:42.261306
\.


--
-- Data for Name: link_tokens; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.link_tokens (id, token, user_id, expires_at, used, created_at) FROM stdin;
1	bbeb7d56-e9d2-4a5c-bec7-40a6fe83be10	1	2026-06-18 19:44:40.037793	t	2026-06-18 19:39:40.315819
2	bb6f2a6a-a45c-423f-917b-56e42e9fe622	1	2026-06-18 20:14:40.84532	t	2026-06-18 20:09:40.985928
\.


--
-- Data for Name: mobile_devices; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.mobile_devices (id, user_id, device_uuid, device_name, platform, is_active, last_seen_at, created_at, refresh_token_hash) FROM stdin;
1	2	3f0555bb-90a6-4b35-9d93-81428541a0ca	Samsung SM-X710	android	t	2026-06-18 19:39:46.457261	2026-06-18 19:39:46.457273	053c8375469b678c9bb04b57b3098bf27292053531583ebde5499d0d3a55dcd1
2	3	204eb76a-70ec-4bb1-b952-4f792610d594	Samsung SM-X710	android	t	2026-06-18 20:10:09.967891	2026-06-18 20:10:09.967903	cba30d2b5d4c0d29efcdaa9bafdd785ce6c7e11f067f7c721d3c23ece0677b09
\.


--
-- Data for Name: notification_channels; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.notification_channels (id, preference_id, channel) FROM stdin;
1	1	app
2	2	app
3	3	app
4	4	app
\.


--
-- Data for Name: notification_days; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.notification_days (id, preference_id, day_of_week) FROM stdin;
1	1	1
2	1	2
3	1	3
4	1	4
5	1	5
6	1	6
7	1	0
8	2	1
9	2	2
10	2	3
11	2	4
12	2	5
13	2	6
14	2	0
15	3	1
16	3	2
17	3	3
18	3	4
19	3	5
20	3	6
21	3	0
22	4	1
23	4	2
24	4	3
25	4	4
26	4	5
27	4	6
28	4	0
\.


--
-- Data for Name: notification_logs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.notification_logs (id, event_id, user_id, channel, status, error_message, sent_at, cooldown_key) FROM stdin;
\.


--
-- Data for Name: notification_preferences; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.notification_preferences (id, user_id, device_id, event_type, camera_id, enabled, schedule_start, schedule_end, created_at) FROM stdin;
1	3	2	person	\N	t	\N	\N	2026-06-18 20:13:21.75887
2	3	2	vehicle	\N	t	\N	\N	2026-06-18 20:13:22.132195
3	3	2	motion	\N	t	\N	\N	2026-06-18 20:13:22.779062
4	3	2	camera_offline	\N	t	\N	\N	2026-06-18 20:13:23.371515
\.


--
-- Data for Name: recordings; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.recordings (id, camera_id, start_time, end_time, file_path, file_size_bytes, duration_seconds, is_favorite) FROM stdin;
8	3	2026-06-18 20:11:22.156907	2026-06-18 20:11:26.608422	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_201122.mp4	647863	4.447873830795288	f
9	3	2026-06-18 20:12:55.131185	2026-06-18 20:13:15.131185	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_201305.mp4	2962220	20	f
10	3	2026-06-18 20:11:27.401099	2026-06-18 20:13:25.114147	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_201127.mp4	13110754	117.71044063568115	f
11	4	2026-06-18 20:11:30.737585	2026-06-18 20:13:40.409146	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_201130.mp4	6880222	129.6644365787506	f
12	3	2026-06-18 20:13:37.431135	2026-06-18 20:13:57.431135	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201347.mp4	1743735	20	f
13	3	2026-06-18 20:15:04.677269	2026-06-18 20:15:24.677269	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_201514.mp4	1818791	20	f
14	3	2026-06-18 20:13:27.876845	2026-06-18 20:15:26.165327	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_201327.mp4	9115875	118.27426767349243	f
15	4	2026-06-18 20:13:42.182185	2026-06-18 20:15:52.726233	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_201342.mp4	5952611	130.49564695358276	f
16	3	2026-06-18 20:15:48.983415	2026-06-18 20:16:08.983415	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201558.mp4	1504264	20	f
17	3	2026-06-18 20:17:05.801293	2026-06-18 20:17:25.801293	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_201715.mp4	3244044	20	f
18	3	2026-06-18 20:15:33.71006	2026-06-18 20:17:31.042142	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_201533.mp4	12718016	117.32074618339539	f
19	4	2026-06-18 20:15:54.566904	2026-06-18 20:18:04.60265	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_201554.mp4	8936569	129.9995722770691	f
20	3	2026-06-18 20:18:22.355728	2026-06-18 20:18:42.355728	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201832.mp4	1971052	20	f
21	3	2026-06-18 20:17:34.035267	2026-06-18 20:19:31.179882	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_201734.mp4	11819315	117.1054093837738	f
22	3	2026-06-18 20:19:14.359049	2026-06-18 20:19:34.359049	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201924.mp4	1605838	20	f
23	3	2026-06-18 20:19:47.863885	2026-06-18 20:20:07.863885	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_201957.mp4	1525256	20	f
24	4	2026-06-18 20:18:06.476913	2026-06-18 20:20:17.362092	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_201806.mp4	5727319	130.8467311859131	f
25	3	2026-06-18 20:19:33.927243	2026-06-18 20:21:31.634991	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_201933.mp4	7455867	117.63973498344421	f
26	4	2026-06-18 20:20:19.167712	2026-06-18 20:22:28.828382	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_202019.mp4	5214532	129.62765097618103	f
27	3	2026-06-18 20:22:13.900203	2026-06-18 20:22:33.900203	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_202223.mp4	1769178	20	f
28	3	2026-06-18 20:21:35.053187	2026-06-18 20:23:31.962364	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_202135.mp4	7750340	116.89535593986511	f
29	3	2026-06-18 20:23:31.368466	2026-06-18 20:23:51.368466	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_202341.mp4	1627266	20	f
30	4	2026-06-18 20:22:30.501445	2026-06-18 20:24:40.222781	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_202230.mp4	5000160	129.6164584159851	f
31	3	2026-06-18 20:23:34.443866	2026-06-18 20:25:31.746198	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_202334.mp4	7492791	117.26552486419678	f
32	4	2026-06-18 20:24:41.948954	2026-06-18 20:26:51.369035	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_202441.mp4	5094520	129.41287088394165	f
33	3	2026-06-18 20:25:34.131625	2026-06-18 20:27:31.437688	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_202534.mp4	7532433	117.29024410247803	f
34	4	2026-06-18 20:26:53.174768	2026-06-18 20:29:02.376644	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_202653.mp4	4494048	129.1926076412201	f
35	3	2026-06-18 20:27:33.862524	2026-06-18 20:29:34.075022	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_202733.mp4	8537959	120.57464027404785	f
36	4	2026-06-18 20:29:03.552927	2026-06-18 20:31:13.003472	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_202903.mp4	4704206	129.44902348518372	f
37	3	2026-06-18 20:29:38.610186	2026-06-18 20:31:35.804292	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_202938.mp4	8073270	117.05574584007263	f
38	4	2026-06-18 20:31:14.169205	2026-06-18 20:33:23.859282	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_203114.mp4	4566846	129.68862009048462	f
39	3	2026-06-18 20:31:38.822716	2026-06-18 20:33:35.664198	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_203138.mp4	8338519	116.83958077430725	f
40	3	2026-06-18 20:35:10.921876	2026-06-18 20:35:30.921876	C:\\nvr_data\\recordings\\3\\events\\event_person_20260618_203520.mp4	1850262	20	f
41	4	2026-06-18 20:33:24.864952	2026-06-18 20:35:36.203392	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_203324.mp4	4677093	131.3198618888855	f
42	3	2026-06-18 20:33:38.397538	2026-06-18 20:35:36.272927	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_203338.mp4	8271788	117.84963774681091	f
43	3	2026-06-18 20:35:39.751591	2026-06-18 20:37:36.574667	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_203539.mp4	7481124	116.82079124450684	f
44	4	2026-06-18 20:35:38.400502	2026-06-18 20:37:48.678118	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_203538.mp4	4540167	130.19853568077087	f
45	3	2026-06-18 20:38:45.088859	2026-06-18 20:39:05.088859	C:\\nvr_data\\recordings\\3\\events\\event_motion_20260618_203855.mp4	1520536	20	f
46	3	2026-06-18 20:37:39.193379	2026-06-18 20:40:40.936704	C:\\nvr_data\\recordings\\3\\continuous\\3_20260618_203739.mp4	5995328	181.65890169143677	f
47	4	2026-06-18 20:37:50.658653	2026-06-18 20:40:41.040054	C:\\nvr_data\\recordings\\4\\continuous\\4_20260618_203750.mp4	2888730	170.31192588806152	f
\.


--
-- Data for Name: revoked_tokens; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.revoked_tokens (id, jti, expires_at, revoked_at, user_id, reason) FROM stdin;
\.


--
-- Data for Name: system_config; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.system_config (id, key, value, updated_at) FROM stdin;
1	telegram_bot_token	8520537600:AAG2LQoESqyRZJsNr_WY3Y1fGCvvHFlFzvE	2026-06-18 19:34:46.50518
2	telegram_chat_id	1383506337	2026-06-18 19:34:46.505193
3	telegram_chat_ids	1383506337	2026-06-18 19:34:46.505199
4	telegram_enabled	true	2026-06-18 19:34:46.505202
5	notify_person	true	2026-06-18 19:34:46.505206
6	notify_vehicle	true	2026-06-18 19:34:46.50521
7	notify_motion	true	2026-06-18 19:34:46.505213
8	notify_camera_offline	true	2026-06-18 19:34:46.505216
9	ai_lens_3	l1	2026-06-18 20:12:47.934572
\.


--
-- Data for Name: telegram_verification_codes; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.telegram_verification_codes (id, user_id, device_id, code, expires_at, used, created_at) FROM stdin;
\.


--
-- Data for Name: user_camera_permissions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_camera_permissions (id, user_id, camera_id, can_view, can_control_ptz, can_control_leds, can_control_audio, can_download_recordings, created_at) FROM stdin;
\.


--
-- Data for Name: user_telegram_chats; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_telegram_chats (id, user_id, device_id, telegram_chat_id, telegram_username, linked_at, is_active) FROM stdin;
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.users (id, username, password_hash, role, is_active, created_at) FROM stdin;
1	admin	pbkdf2:sha256:1000000$TaQYlQtvCW0JxROr$cee8e9553b97f1e63cd15b610b9a4dc4501a32070004168ba7880dca73941b13	admin	t	2026-06-18 19:37:03.862708
2	mobile_3e065fab6f09444ab16e8599845c7420	pbkdf2:sha256:1000000$dtCb2pAZQFWQ1QK5$dc1114526da0888a4482f0f8513579cfdc12b5f31ff3868ce5606df314c2bc00	mobile	t	2026-06-18 19:39:46.453847
3	mobile_41737b2bde01479892a2a4896a47ee42	pbkdf2:sha256:1000000$t0ZPxPKWp4NGqNVb$6de7cef13d90320785abc565ccd6e1bc95c9a6819131de98a61ea3cc27588a0b	mobile	t	2026-06-18 20:10:09.966224
\.


--
-- Name: audit_logs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.audit_logs_id_seq', 7, true);


--
-- Name: cameras_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.cameras_id_seq', 4, true);


--
-- Name: events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.events_id_seq', 13, true);


--
-- Name: link_tokens_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.link_tokens_id_seq', 2, true);


--
-- Name: mobile_devices_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.mobile_devices_id_seq', 2, true);


--
-- Name: notification_channels_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.notification_channels_id_seq', 4, true);


--
-- Name: notification_days_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.notification_days_id_seq', 28, true);


--
-- Name: notification_logs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.notification_logs_id_seq', 1, false);


--
-- Name: notification_preferences_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.notification_preferences_id_seq', 4, true);


--
-- Name: recordings_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.recordings_id_seq', 47, true);


--
-- Name: revoked_tokens_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.revoked_tokens_id_seq', 1, false);


--
-- Name: system_config_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.system_config_id_seq', 9, true);


--
-- Name: telegram_verification_codes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.telegram_verification_codes_id_seq', 1, false);


--
-- Name: user_camera_permissions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_camera_permissions_id_seq', 1, false);


--
-- Name: user_telegram_chats_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_telegram_chats_id_seq', 1, false);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.users_id_seq', 3, true);


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

