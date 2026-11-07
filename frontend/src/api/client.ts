export interface HealthResponse {
  status: string;
  database: string;
}

export interface BusinessHour {
  /** 0=월요일 ... 6=일요일 */
  day_of_week: number;
  open_time: string | null;
  close_time: string | null;
  is_closed: boolean;
}

export interface Store {
  slug: string;
  name: string;
  description: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  website: string | null;
  timezone: string;
  business_hours: BusinessHour[];
}

export async function fetchStore(): Promise<Store> {
  const res = await fetch("/api/store");
  if (!res.ok) {
    throw new Error(`Store request failed: ${res.status}`);
  }
  return res.json() as Promise<Store>;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/api/health");
  if (!res.ok) {
    throw new Error(`Health check failed: ${res.status}`);
  }
  return res.json() as Promise<HealthResponse>;
}
