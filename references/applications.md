# Useful site-owner applications

These patterns are optional tasks, not installed background jobs.

## Explain a site problem from one scoped snapshot

- **Trigger:** a site error, changed PHP behavior or an app that stopped.
- **Action/benefit:** read the website, available features, selected runtime and relevant app/backup state in one bounded batch, then investigate the actual failing layer.
- **Distinctive approach:** correlate control-panel settings with the website's own runtime rather than treating every failure as DNS or restarting everything.
- **Inputs:** your approved profile, site and failing URL/error.
- **Smallest prototype:** site + persistent-app metadata, with a separate approved application-health check.
- **Risk/unknown:** logs may contain secrets and control-plane health doesn't prove app health. Simpler alternative: one exact panel view when the problem is already identified.
- **Value test:** find the cause and verify the smallest fix without changing unrelated services.

## A reviewed hosting change

- **Trigger:** add a DNS record, mailbox, database or runtime setting.
- **Action/benefit:** inspect the current record, prepare a local request plan, apply the approved change and read it back.
- **Distinctive approach:** reuse your configured IDs and schema descriptions while retaining explicit effect and rollback checks.
- **Inputs:** the exact desired values and your current permission/package allowance.
- **Smallest prototype:** a prepared request for one resource; live writes need their actual approval.
- **Risk/unknown:** mail/DNS/password changes may have wider effects than one record. Simpler alternative: make the one change in the panel.
- **Value test:** the intended service behavior works and all unrelated settings remain unchanged.

## Application recovery checklist

- **Trigger:** a website backup has been restored or an app is moving.
- **Action/benefit:** reconcile files, DB grants, cron and persistent-app configuration; redeploy only the intended app and test its URL.
- **Distinctive approach:** treat restored files and a running service as separate acceptance checks.
- **Inputs:** the selected backup, intended app state and approved file access.
- **Smallest prototype:** a read-only recovery-gap checklist. A real restore/restart is a separate action.
- **Risk/unknown:** restores can overwrite newer data and may not restore every credential or schedule. Simpler alternative: provider-assisted restore using the same scope checks.
- **Value test:** your app, data and required scheduled work function after recovery.
