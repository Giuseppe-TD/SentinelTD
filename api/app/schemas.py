from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SiteIn(BaseModel):
    name: str
    url: str
    cms: str                      # 'wp' | 'joomla'
    token: str                    # copiato dal connettore installato sul sito
    admin_url: str = ""
    group: str = ""
    tags: str = ""
    enabled: bool = True
    auto_update: bool = False
    poll_interval_minutes: int = 180
    notifications_silenced: bool = False


class SiteUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    cms: str | None = None
    token: str | None = None
    admin_url: str | None = None
    group: str | None = None
    tags: str | None = None
    enabled: bool | None = None
    auto_update: bool | None = None
    poll_interval_minutes: int | None = None
    notifications_silenced: bool | None = None


class ExtensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    type: str
    name: str
    slug: str
    current_version: str
    new_version: str
    update_available: bool


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    url: str
    cms: str
    admin_url: str
    group: str
    tags: str
    enabled: bool
    auto_update: bool
    poll_interval_minutes: int
    notifications_silenced: bool
    last_checked: datetime | None
    status: str
    error: str
    core_current: str
    core_latest: str
    core_update: bool
    updates_count: int
    upd_plugins: int
    tot_plugins: int
    upd_themes: int
    tot_themes: int
    upd_other: int
    tot_other: int
    php_version: str
    domain_name: str
    domain_expires_at: datetime | None
    domain_checked_at: datetime | None
    domain_check_error: str
    shot_path: str
    shot_at: datetime | None


class SiteDetailOut(SiteOut):
    # il token (bearer admin-equivalent del connettore) viene esposto SOLO nel
    # dettaglio del singolo sito, mai nella lista /api/sites: limita la superficie
    # di leak (un solo sito alla volta, e solo quando apri il pannello dettaglio).
    token: str
    extensions: list[ExtensionOut] = []


class LoginIn(BaseModel):
    password: str


class BulkTagsIn(BaseModel):
    site_ids: list[int]
    add: list[str] = []        # tag da aggiungere ai siti selezionati
    remove: list[str] = []     # tag da rimuovere dai siti selezionati


class SiteExpiryIn(BaseModel):
    name: str
    category: str = "Licenza"
    platform: str = "both"
    provider: str = ""
    notes: str = ""
    expires_at: datetime
    recur_months: int = 0


class SiteExpiryUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    platform: str | None = None
    provider: str | None = None
    notes: str | None = None
    expires_at: datetime | None = None
    recur_months: int | None = None


class SiteExpiryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    site_id: int | None
    name: str
    category: str
    platform: str
    provider: str
    notes: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime | None
