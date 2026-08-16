"use client";

import { useEffect, useState } from "react";
import { analytics, settings, ApiError, type ApiKeyInfo, type DocumentTypeInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/utils";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, Input } from "@/components/ui";
import { useToast } from "@/components/toast-context";

export default function SettingsPage() {
  const { session } = useAuth();
  const [types, setTypes] = useState<DocumentTypeInfo[] | null>(null);
  const [keys, setKeys] = useState<ApiKeyInfo[] | null>(null);
  const [newKeyName, setNewKeyName] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const notify = useToast();

  useEffect(() => {
    analytics.documentTypes().then(setTypes);
    settings.apiKeys().then(setKeys);
  }, []);

  async function createKey() {
    if (!newKeyName.trim()) return;
    setCreating(true);
    try {
      const created = await settings.createApiKey(newKeyName.trim());
      setRevealedKey(created.api_key ?? null);
      setKeys(await settings.apiKeys());
      setNewKeyName("");
    } catch (err) {
      notify("danger", err instanceof ApiError ? err.message : "Could not create key.");
    } finally {
      setCreating(false);
    }
  }

  async function revokeKey(id: string) {
    try {
      await settings.revokeApiKey(id);
      setKeys(await settings.apiKeys());
      notify("brand", "API key revoked.");
    } catch (err) {
      notify("danger", err instanceof ApiError ? err.message : "Could not revoke key.");
    }
  }

  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Settings</h1>
        <p className="mt-1 text-sm text-subtle">Organization, document types, API keys and exports.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Organization</CardTitle>
        </CardHeader>
        <CardBody className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-xs text-subtle">Name</p>
            <p className="mt-0.5 font-medium text-ink">{session?.organization.name}</p>
          </div>
          <div>
            <p className="text-xs text-subtle">Plan</p>
            <p className="mt-0.5 font-medium capitalize text-ink">{session?.organization.plan}</p>
          </div>
          <div>
            <p className="text-xs text-subtle">Monthly quota</p>
            <p className="mt-0.5 font-medium text-ink">
              {session?.organization.monthly_document_quota === 0
                ? "Unlimited"
                : `${session?.organization.monthly_document_quota} documents`}
            </p>
          </div>
          <div>
            <p className="text-xs text-subtle">Your role</p>
            <p className="mt-0.5 font-medium capitalize text-ink">{session?.organization.role}</p>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Document types</CardTitle>
        </CardHeader>
        <CardBody className="space-y-3">
          {!types && <p className="text-sm text-subtle">Loading…</p>}
          {types?.map((type) => (
            <div key={type.key} className="rounded-lg border border-line px-4 py-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-ink">{type.name}</p>
                <Badge tone="neutral">{type.field_count} fields</Badge>
              </div>
              <p className="mt-1 text-xs text-subtle">{type.description}</p>
              <p className="mt-1.5 text-xs text-subtle">
                {type.required_fields.length} required · {type.critical_fields.length} critical ·
                review threshold {(type.review_threshold * 100).toFixed(0)}%
              </p>
            </div>
          ))}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Export</CardTitle>
        </CardHeader>
        <CardBody className="flex items-center gap-3">
          <a href={analytics.exportUrl("csv")}>
            <Button variant="outline" size="sm">
              Export CSV
            </Button>
          </a>
          <a href={analytics.exportUrl("json")}>
            <Button variant="outline" size="sm">
              Export JSON
            </Button>
          </a>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>API keys</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4">
          {revealedKey && (
            <div className="rounded-lg bg-brand/10 px-4 py-3">
              <p className="text-xs font-semibold text-brand">
                Copy this key now — it will not be shown again
              </p>
              <code className="mt-1.5 block break-all rounded bg-surface px-2 py-1.5 text-xs">
                {revealedKey}
              </code>
              <Button size="sm" variant="ghost" className="mt-2" onClick={() => setRevealedKey(null)}>
                Done
              </Button>
            </div>
          )}

          <div className="flex gap-2">
            <Input
              placeholder="Key name (e.g. n8n integration)"
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
            />
            <Button size="sm" loading={creating} onClick={createKey}>
              Create key
            </Button>
          </div>

          <div className="divide-y divide-line rounded-lg border border-line">
            {keys?.length === 0 && (
              <p className="px-4 py-3 text-sm text-subtle">No API keys yet.</p>
            )}
            {keys?.map((key) => (
              <div key={key.id} className="flex items-center justify-between px-4 py-3">
                <div>
                  <p className="text-sm font-medium text-ink">{key.name}</p>
                  <p className="font-mono text-xs text-subtle">
                    {key.prefix}… ·{" "}
                    {key.last_used_at ? `used ${formatDateTime(key.last_used_at)}` : "never used"}
                  </p>
                </div>
                {key.revoked_at ? (
                  <Badge tone="danger">Revoked</Badge>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => revokeKey(key.id)}>
                    Revoke
                  </Button>
                )}
              </div>
            ))}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
