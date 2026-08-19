# OptiFlow — Streamlit front-end

A web-based sibling of the desktop app (`optiflow_gui.py`, one folder up). It
talks to the exact same AWS pipeline described in `OptiFlow_Project_Record.docx`
and `README.md` one level up: sign in, answer the engine's two Yes/No
questions, press Run, and the reports arrive by email. Nothing here runs the
engine locally, and nothing here needs AWS credentials — it only POSTs to the
same HTTPS API Gateway endpoint the desktop app uses.

This folder is fully self-contained: it does not import or read anything from
outside itself at runtime. For local, desktop-adjacent use it will pick up
`../optiflow_config.json` and `../optiflow_users.json` if they're sitting there
(handy when running this next to the desktop app), but neither is required —
everything can equally be supplied through environment variables, which is
what the containerized deployment below does. It never writes to any file
outside this folder either way.

Because it's self-contained, this folder can be its own git repository (its
own GitLab project) independent of everything else in `DistributerPlanning/`.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Configuration

- `OPTIFLOW_API_URL` (env var) — the API endpoint. Takes precedence over
  `../optiflow_config.json`.
- `OPTIFLOW_API_KEY` (env var) — optional auth header value.
- `OPTIFLOW_USERS_JSON` (env var) — accounts, as the same JSON shape as
  `optiflow_users.json` (`{"users": [...]}`). Takes precedence over a sibling
  `../optiflow_users.json` file. This is how accounts reach the app in a
  container, without ever committing real password hashes to a repo.
- `../optiflow_config.json` / `../optiflow_users.json` — local fallbacks, used
  only when the env vars above aren't set.

For local development, copy `.env.example` to `.env` (same folder as `app.py`)
and fill in real values there instead of exporting env vars by hand each
session:
```bash
cp .env.example .env
# edit .env, set OPTIFLOW_API_KEY etc.
```
`app.py` loads `.env` automatically on startup via `python-dotenv`. `.env` is
git-ignored — it never gets committed, and it's excluded from the Docker build
context too. A real deployment (Lightsail, GitLab CI/CD) sets these as actual
environment variables instead — `.env` is a local-only convenience and is a
no-op if it isn't there.

## Notes

- Sign-in here is identity only, same caveat as the desktop app: it decides
  which address SES mails, it is not access control.
- If no accounts exist, the app runs unattributed and you type the recipient
  by hand on the Email tab.

## Deploying: GitLab + AWS Lightsail Containers (no EC2, no long-lived AWS keys)

This ships the app as a container, always running (Streamlit needs a
persistent connection per session, so it can't run in the pay-per-request
Lambda model the rest of this project uses — the running-cost section of
`OptiFlow_Project_Record.docx` already flags this exact trade-off). GitLab
CI/CD builds the image and deploys it straight to an AWS Lightsail Container
Service on every push to `main`. AWS credentials are never stored in
GitLab — the pipeline authenticates via OIDC federation instead
(`sts:AssumeRoleWithWebIdentity`), so GitLab hands the job a short-lived,
pipeline-scoped token and AWS exchanges it for temporary credentials.

Files involved: `Dockerfile`, `.dockerignore`, `.gitlab-ci.yml`,
`deploy/public-endpoint.json`, `deploy/make_containers_json.py`.

### One-time AWS setup (in your AWS account, outside this repo)

1. **Create the Lightsail Container Service** (the pipeline deploys *into*
   this; it doesn't create it):
   ```bash
   aws lightsail create-container-service \
     --service-name optiflow-streamlit \
     --power nano --scale 1 \
     --region ap-south-1
   ```
   `nano` is the cheapest tier (~US$7/month, always-on).

2. **Create an IAM OIDC identity provider** for GitLab, in the IAM console
   (or CLI):
   - Provider URL: `https://gitlab.com` (or your self-managed GitLab's URL).
   - Audience: `https://gitlab.com` (must exactly match the `aud` value in
     `.gitlab-ci.yml`'s `id_tokens` block — edit that file if your GitLab URL
     differs).

3. **Create an IAM role** the pipeline will assume, with this trust policy
   (replace `<ACCOUNT_ID>`, `<your-group>/<your-project>`):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {
         "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/gitlab.com"
       },
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": { "gitlab.com:aud": "https://gitlab.com" },
         "StringLike": {
           "gitlab.com:sub": "project_path:<your-group>/<your-project>:ref_type:branch:ref:main"
         }
       }
     }]
   }
   ```
   The `sub` condition means *only* pipelines running on `main` in *this*
   GitLab project can assume the role — nothing else can.

4. **Attach a permissions policy** to that role, scoped to just Lightsail:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": [
         "lightsail:PushContainerImage",
         "lightsail:CreateContainerServiceDeployment",
         "lightsail:GetContainerServices",
         "lightsail:GetContainerServiceDeployments",
         "lightsail:GetContainerImages"
       ],
       "Resource": "*"
     }]
   }
   ```

5. Note the role's ARN — you'll need it below.

### One-time GitLab setup (project → Settings → CI/CD → Variables)

| Variable | Value | Flags |
|---|---|---|
| `AWS_ROLE_ARN` | the IAM role ARN from step 3/5 above | Protected |
| `AWS_REGION` | e.g. `ap-south-1` | Protected |
| `LIGHTSAIL_SERVICE_NAME` | `optiflow-streamlit` (or whatever you named it) | Protected |
| `OPTIFLOW_API_URL` | the API Gateway URL | Protected |
| `OPTIFLOW_API_KEY` | the API key, if the endpoint requires one | Protected, Masked |
| `OPTIFLOW_USERS_JSON` | the full contents of `optiflow_users.json` | Protected, Masked |

Mark `main` as a protected branch (Settings → Repository → Protected
branches) — `Protected` variables are only exposed to pipelines running on
protected branches, which is what makes the IAM trust policy's `ref:main`
restriction actually mean something.

### After that

Every push to `main` runs the `deploy` job: builds the image, pushes it into
Lightsail's own container registry (no ECR needed — `push-container-image` is
already the registry push for Lightsail Containers), and rolls out a new
deployment with the env vars above injected at the container level, never
baked into the image or committed to the repo.

Find the live URL with:
```bash
aws lightsail get-container-services --service-name optiflow-streamlit \
  --query 'containerServices[0].url' --output text
```
