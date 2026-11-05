# Deploying LegalLens to AKS (CD plan)

This is the to-do list for taking LegalLens from "CI passes" to "running on Azure Kubernetes Service (AKS)".
It is deliberately **minimal**: one small cluster, plain HTTP on a public IP, no domain name.
It is a learning setup, **not** a production setup. Section 10 lists what we left out.

Work through the steps in order. Do steps 1 to 6 by hand first. Automate them (step 9) only once a manual deploy works.

---

## What we will end up with

```
 GitHub Actions (CD)
 logs in with OIDC, no stored password
        │
        │ push images
        ▼
 ┌─ Resource group: rg-legallens ─────────────────────────────────────┐
 │                                                                     │
 │  ┌─ ACR: legallensacrXXXX ─┐  pull    ┌─ AKS: aks-legallens ─────┐  │
 │  │  legallens-backend      │ ───────► │  1 node                  │  │
 │  │  legallens-frontend     │          │                          │  │
 │  └─────────────────────────┘          │  frontend  (nginx :8080) │◄─┼── Browser
 │                                       │     │ /api               │  │   http://<public IP>
 │                                       │     ▼                    │  │
 │                                       │  backend   (:8000)       │  │
 │                                       │     │                    │  │
 │                                       │     ▼                    │  │
 │                                       │  Azure Disk, 20 GB       │  │
 │                                       │  SQLite, uploads,        │  │
 │                                       │  vector index, models    │  │
 │                                       └──────────────────────────┘  │
 └─────────────────────────────────────────────────────────────────────┘
```

### Resources at a glance

| # | Resource | Name used in this guide | Why we need it |
|---|---|---|---|
| 1 | Azure account and subscription | (yours) | Everything is billed to a subscription |
| 2 | Resource group | `rg-legallens` | One folder for all our resources, so we can delete everything in one go |
| 3 | Azure Container Registry (ACR), Basic tier | `legallensacrXXXX` | Stores our two Docker images |
| 4 | AKS cluster, Free tier, 1 node | `aks-legallens` | Runs the containers |
| 5 | Kubernetes namespace, secret, disk, 2 deployments, 2 services | `legallens` | The app itself, inside the cluster |
| 6 | Microsoft Entra app registration with a federated credential | `gh-legallens-cd` | Lets GitHub Actions log in to Azure without a stored password |
| 7 | GitHub repository secrets | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Tells the CD pipeline which identity and subscription to use |

AKS also creates a second resource group by itself, named `MC_rg-legallens_aks-legallens_<region>`.
It holds the VM, disk, load balancer and public IP. **Do not edit it by hand.** It is deleted together with the cluster.

---

## Before you start

### Tools to install on your machine

| Tool | Check it works with | Install |
|---|---|---|
| Azure CLI | `az version` | https://learn.microsoft.com/cli/azure/install-azure-cli |
| kubectl | `kubectl version --client` | `az aks install-cli` (after installing Azure CLI) |
| GitHub CLI (optional, for step 8) | `gh --version` | https://cli.github.com |

You do **not** need Docker on your machine for this guide: images are built inside Azure (step 5.1).

### Names and settings used in every command

| Setting | Value in this guide | Change it? |
|---|---|---|
| Region | `eastus` | Optional. Pick one near you, then use it everywhere. |
| Resource group | `rg-legallens` | Optional |
| ACR name | `legallensacrXXXX` | **Required.** ACR names are global across all of Azure. Replace `XXXX` with your initials or 4 random digits, lowercase letters and digits only, in **every** command and YAML file below. |
| AKS cluster | `aks-legallens` | Optional |
| Node size | `Standard_D4s_v4` (4 vCPU, 16 GB RAM) | See the note in step 4 |
| Kubernetes namespace | `legallens` | Optional |

> **About the commands.** Every `az` and `kubectl` command is written on a single line, so it pastes unchanged into Windows CMD, PowerShell, or a macOS/Linux terminal.
> Where a command prints a value you need later (an ID, an IP address), the step tells you to copy it.

---

## To-do list

- [ ] 1. Create an Azure account and sign in
- [ ] 2. Create a resource group
- [ ] 3. Create the container registry (ACR)
- [ ] 4. Create the Kubernetes cluster (AKS) and connect `kubectl` to it
- [ ] 5. Build the images and push them to ACR
- [ ] 6. Deploy LegalLens to AKS by hand and check it works
- [ ] 7. Create an identity for GitHub Actions
- [ ] 8. Add the Azure IDs to GitHub as secrets
- [ ] 9. Let the CD pipeline deploy every merge to `main`
- [ ] 10. Know what we left out
- [ ] 11. Clean up when you are done

---

## 1. Create an Azure account and sign in

### Web interface

1. Go to https://azure.microsoft.com/free and create a free account.
   Students can use **Azure for Students** instead (https://azure.microsoft.com/free/students), which needs no credit card.
2. Sign in to the portal at https://portal.azure.com.
3. Search for **Subscriptions** in the top search bar and check you have one with status **Active**.
4. Open the subscription, then **Settings → Resource providers**. Search for and **Register** each of these, if they are not already registered:
   - `Microsoft.ContainerService` (AKS)
   - `Microsoft.ContainerRegistry` (ACR)

   New subscriptions often have these off. Without them, creating ACR or AKS fails.

### Azure CLI

```
az login
az account show --output table
```

If you have more than one subscription, pick the one to use:

```
az account list --output table
az account set --subscription "<subscription name or id>"
```

Register the two resource providers (this takes a minute or two, safe to run again):

```
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.ContainerRegistry
az provider show --namespace Microsoft.ContainerService --query registrationState
```

Wait until the last command prints `"Registered"`.

> **Quota check (free trial and student accounts).** Azure limits vCPUs **per VM family, per region**, and some families are set to **0** on these accounts.
> Our single `Standard_D4s_v4` node needs 4 vCPUs in the **Standard DSv4 Family**. Check before step 4:
>
> ```
> az vm list-usage --location eastus --output table
> ```
>
> Look at the `Limit` column for `Standard DSv4 Family vCPUs` and `Total Regional vCPUs`. Both need at least 4 free. A bigger node, or a second node, will be refused with a quota error.

---

## 2. Create a resource group

A resource group is a folder for related resources. Deleting it deletes everything inside it.

### Web interface

1. Search for **Resource groups** → **Create**.
2. **Subscription:** yours. **Resource group:** `rg-legallens`. **Region:** `East US` (or the region you chose).
3. **Review + create** → **Create**.

### Azure CLI

```
az group create --name rg-legallens --location eastus
```

---

## 3. Create the container registry (ACR)

ACR is a private Docker registry. CI already proves the images build; ACR is where CD stores them so AKS can pull them.

### Web interface

1. Search for **Container registries** → **Create**.
2. **Resource group:** `rg-legallens`. **Registry name:** `legallensacrXXXX`. **Location:** same region. **Pricing plan:** `Basic`.
3. Leave everything else at its default. **Review + create** → **Create**.

### Azure CLI

```
az acr create --resource-group rg-legallens --name legallensacrXXXX --sku Basic
```

The registry's address is `legallensacrXXXX.azurecr.io`. You will see it in the image names later.

We leave the **admin user disabled**. AKS gets pull access through its managed identity in step 4, and GitHub gets push access through its own identity in step 7, so no registry password exists anywhere.

---

## 4. Create the Kubernetes cluster (AKS)

### Choosing the node size

The backend loads two AI models (`bge-m3` and `bge-reranker-v2-m3`, about 2.2 GB each) into memory, on CPU.
That needs a node with **16 GB of RAM**. We use `Standard_D4s_v4` (4 vCPU, 16 GB, Intel), about $0.19 per hour in `eastus`.

> **Why not a B-series size?** They are cheaper, but on free and student subscriptions they often fail:
> `Standard_B4ms` with `The VM size of Standard_B4ms is not allowed in your subscription`, and `Standard_B4s_v2` with `Insufficient vcpu quota requested 4, remaining 0 for family standardBsv2Family`.
>
> If `Standard_D4s_v4` also fails, run the `az vm list-usage` command from step 1 and pick another 4 vCPU / 16 GB size whose family shows a limit of at least 4, for example `Standard_D4as_v4` (DASv4 family) or `Standard_D4s_v6` (Dsv6 family).
> Avoid names with **`p`** (for example `d4ps_v5`: Arm CPUs, our amd64 images will not start) and names with **`l`** (for example `d4ls_v5`: only 8 GB RAM).
>
> The node costs money while it runs. Stop the cluster when you are not using it: `az aks stop --resource-group rg-legallens --name aks-legallens` (and `az aks start` to resume).

### Web interface

1. Search for **Kubernetes services** → **Create** → **Kubernetes cluster**.
2. **Basics** tab:
   - **Resource group:** `rg-legallens`
   - **Cluster preset configuration:** `Dev/Test`
   - **Kubernetes cluster name:** `aks-legallens`
   - **Region:** same region
   - **Pricing tier:** `Free`
   - **Authentication and Authorization:** `Local accounts with Kubernetes RBAC`
3. **Node pools** tab: click the `agentpool` (or `nodepool1`) row and set:
   - **Node size:** `Standard_D4s_v4`
   - **Scale method:** `Manual`, **Node count:** `1`
4. **Integrations** tab: **Container registry** → select `legallensacrXXXX`.
   This is what lets the cluster pull our images without a password.
5. Turn off anything that adds cost and that we do not need yet, such as **Container insights / Azure Monitor** and **Azure Policy**, if the preset turned them on.
6. **Review + create** → **Create**. This takes 5 to 10 minutes.
7. When it finishes, open the cluster and click **Connect**. It shows the two commands from the CLI section below. Run them on your machine to connect `kubectl`.

### Azure CLI

macOS / Linux:

```bash
az aks create \
  --resource-group  rg-legallens \
  --name            aks-legallens \
  --tier            free \
  --node-count      1 \
  --node-vm-size    Standard_D4s_v4 \
  --attach-acr      legallensacrXXXX \
  --generate-ssh-keys
```

Windows CMD or PowerShell (the same command on one line):

```
az aks create --resource-group rg-legallens --name aks-legallens --tier free --node-count 1 --node-vm-size Standard_D4s_v4 --attach-acr legallensacrXXXX --generate-ssh-keys
```

`--attach-acr` gives the cluster's identity the **AcrPull** role on the registry.

| Flag | Value | Meaning |
|---|---|---|
| `--resource-group` | `rg-legallens` | The resource group the cluster goes into |
| `--name` | `aks-legallens` | The cluster's name |
| `--tier` | `free` | Free control plane; you pay only for the node VM |
| `--node-count` | `1` | Number of worker machines (nodes) |
| `--node-vm-size` | `Standard_D4s_v4` | Size of each node: 4 vCPU, 16 GB RAM |
| `--attach-acr` | `legallensacrXXXX` | Lets the cluster pull images from our registry without a password |
| `--generate-ssh-keys` | (none) | Uses your key in `~/.ssh`, or creates one if missing |

`--attach-acr` needs your **real** registry name, not `legallensacrXXXX`. If you do not remember it, list the registries in the resource group:

```
az acr list --resource-group rg-legallens --query "[].name" --output tsv
```

If this prints nothing, the registry does not exist yet. Go back to step 3.

#### If it fails with `MissingSubscriptionRegistration`

```
(MissingSubscriptionRegistration) The subscription is not registered to use namespace 'Microsoft.ContainerService'.
```

The resource providers from step 1 are not registered yet. Register them, wait, then run `az aks create` again:

```
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.ContainerRegistry
az provider show --namespace Microsoft.ContainerService --query registrationState --output tsv
```

Repeat the last command until it prints `Registered` (not `Registering`). This usually takes a few minutes.

#### Check the cluster

```
az aks show --resource-group rg-legallens --name aks-legallens --query "{name:name, state:provisioningState, power:powerState.code, k8s:kubernetesVersion, location:location}" --output table
az resource list --resource-group rg-legallens --query "[].{name:name, type:type}" --output table
```

The first should show `state` as `Succeeded` and `power` as `Running`. The second lists everything in the resource group.

### Connect kubectl to the cluster (needed for both ways)

```
az aks get-credentials --resource-group rg-legallens --name aks-legallens
kubectl get nodes
```

You should see one node with status `Ready`.

---

## 5. Build the images and push them to ACR

`az acr build` uploads the source folder to Azure and builds the image there, then stores it in ACR.
This needs no local Docker, and it builds for `linux/amd64`, which is what AKS nodes run.
(If you build locally on an Apple Silicon Mac, you get an `arm64` image that will not start on AKS.)

Run these from the repository root:

```
az acr build --registry legallensacrXXXX --image legallens-backend:v1 ./backend
az acr build --registry legallensacrXXXX --image legallens-frontend:v1 ./frontend
```

The backend build takes several minutes; the image is about 2.5 GB.

### Check the images in the web interface

The portal cannot build or upload images, but you can see them there:
**Container registries → legallensacrXXXX → Services → Repositories**. You should see `legallens-backend` and `legallens-frontend`, each with tag `v1`.

### Check the images in the CLI

```
az acr repository list --name legallensacrXXXX --output table
az acr repository show-tags --name legallensacrXXXX --repository legallens-backend --output table
```

---

## 6. Deploy LegalLens to AKS by hand

Before we automate anything, we deploy once by hand to prove that the images, settings and storage all work.

### How the app maps onto Kubernetes

| Piece | Kubernetes object | Notes |
|---|---|---|
| Backend settings that are secrets | `Secret` named `backend-secrets` | Encryption key, session key, Anthropic API key, seed user password |
| Backend data folder `/app/data` | `PersistentVolumeClaim`, 20 GB Azure Disk | Holds the SQLite database, encrypted uploads, the vector index and the downloaded models. Survives pod restarts. |
| Backend | `Deployment` with **1 replica**, `Service` of type `ClusterIP` | Reachable only inside the cluster, at `http://backend:8000` |
| Frontend | `Deployment` with 1 replica, `Service` of type `LoadBalancer` | Azure gives this service a public IP address. nginx serves the web app and forwards `/api` to the backend. |

> **Why the backend must stay at exactly 1 replica.** SQLite and the embedded vector store (Qdrant) are files on one disk,
> and Qdrant takes an exclusive lock on its folder. An Azure Disk can also only be attached to one node at a time.
> That is also why the backend uses the `Recreate` rollout strategy: the old pod stops before the new one starts.
> Running more than one backend means moving to Postgres and a Qdrant server first (see section 10).

### 6.1 Create the namespace

**CLI:**

```
kubectl create namespace legallens
```

**Web interface:** open the cluster → **Kubernetes resources → Namespaces → Create → Namespace**, name it `legallens`.

### 6.2 Create the backend secret

The backend refuses to start with `LL_ENV=production` unless `LL_BLOB_KEY` and `LL_SECRET_KEY` are set.
Generate two different random values (run this twice, any machine with Python):

```
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Keep `LL_BLOB_KEY` somewhere safe.** Every uploaded document is encrypted with it. If you lose it, every stored document is lost.

**CLI, macOS / Linux (zsh).** Paste these lines one at a time. They generate the two keys for you and ask for the API key and the demo password without showing them on screen or saving them in your shell history:

```zsh
kubectl create namespace legallens 2>/dev/null || true
LL_BLOB_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
LL_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
read -s "ANTHROPIC_API_KEY?Anthropic API key: "; echo
read -s "LL_SEED_PASSWORD?Password for the demo user: "; echo
kubectl create secret generic backend-secrets --namespace legallens --from-literal=LL_BLOB_KEY="$LL_BLOB_KEY" --from-literal=LL_SECRET_KEY="$LL_SECRET_KEY" --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" --from-literal=LL_SEED_PASSWORD="$LL_SEED_PASSWORD"
```

In bash, use `read -s -p "Anthropic API key: " ANTHROPIC_API_KEY` instead of the zsh `read` lines.

**CLI, Windows** (one line; replace the four values):

```
kubectl create secret generic backend-secrets --namespace legallens --from-literal=LL_BLOB_KEY=<first value> --from-literal=LL_SECRET_KEY=<second value> --from-literal=ANTHROPIC_API_KEY=<your sk-ant-... key> --from-literal=LL_SEED_PASSWORD=<a password for the demo user>
```

> **`zsh: parse error near '\n'`** means a `<...>` placeholder was left in the command. The shell reads `<` and `>` as file redirection. Replace every placeholder, angle brackets included, or use the zsh lines above.
>
> **Never paste a real API key into a chat, a screenshot or a shared terminal log.** If that happens, revoke it in the Anthropic Console and create a new one.

Check the secret. This lists the four key names and their sizes, not the values:

```
kubectl describe secret backend-secrets --namespace legallens
```

Copy `LL_BLOB_KEY` into a password manager now. You can read it back from the cluster later:

```zsh
kubectl get secret backend-secrets --namespace legallens -o jsonpath='{.data.LL_BLOB_KEY}' | base64 -d; echo
```

Made a typo? Delete the secret, run the `kubectl create secret` line above again, then restart the backend so it reads the new values (the restart only matters once the backend is deployed):

```
kubectl delete secret backend-secrets --namespace legallens
kubectl rollout restart deployment/backend --namespace legallens
```

**Web interface:** open the cluster → **Kubernetes resources → Configuration → Secrets → Create → Secret**.
Namespace `legallens`, name `backend-secrets`, then add the same four keys and values.

No Anthropic API key? Leave `ANTHROPIC_API_KEY` out and set `LL_AI_ENGINE` to `fixture` in the backend YAML below.
The app then answers from built-in sample responses instead of Claude.

### 6.3 The Kubernetes manifests

The manifests live in the `k8s/` folder at the repository root. The CD pipeline (step 9) applies the same files.

| File | What it creates |
|---|---|
| `k8s/namespace.yaml` | The `legallens` namespace |
| `k8s/backend.yaml` | The 20 GB disk (`PersistentVolumeClaim`), the backend `Deployment` (1 replica, `Recreate`) and its `ClusterIP` service |
| `k8s/frontend.yaml` | The frontend `Deployment` and its `LoadBalancer` service with a public IP |

The image lines are templates, not fixed names:

```yaml
image: ${ACR_LOGIN_SERVER}/legallens-backend:${IMAGE_TAG}
```

`envsubst` fills in the two variables when the files are applied, so the registry name and tag are never edited by hand and every deploy points to one exact image.
The pipeline uses the commit SHA as the tag. By hand we use the `v1` images from step 5.

Because nginx forwards `/api` to the backend, the browser only ever talks to one address.
The session cookie and CORS settings therefore need no changes.

### 6.4 Apply the manifests

**CLI** (macOS / Linux, from the repository root; replace `legallensacrXXXX`):

```
export ACR_LOGIN_SERVER=legallensacrXXXX.azurecr.io IMAGE_TAG=v1
kubectl apply -f k8s/namespace.yaml
envsubst '${ACR_LOGIN_SERVER} ${IMAGE_TAG}' < k8s/backend.yaml | kubectl apply -f -
envsubst '${ACR_LOGIN_SERVER} ${IMAGE_TAG}' < k8s/frontend.yaml | kubectl apply -f -
kubectl get pods --namespace legallens --watch
```

`envsubst` comes with Git for Windows (Git Bash) and with `brew install gettext` on macOS. On Windows, run these lines in Git Bash.

Wait until both pods show `1/1 Running`, then press Ctrl+C.

Check a file before applying it, without changing the cluster:

```
envsubst '${ACR_LOGIN_SERVER} ${IMAGE_TAG}' < k8s/backend.yaml | kubectl apply --dry-run=server -f -
```

**Web interface:** open the cluster → **Kubernetes resources → Workloads → Create → Apply a YAML**.
Paste `k8s/namespace.yaml` and click **Add**. Then paste `k8s/backend.yaml`, replace the two `${...}` values in the `image:` line by hand, and click **Add**. Do the same for `k8s/frontend.yaml`.
Watch the pods under **Workloads → Pods**, with the namespace filter set to `legallens`.

### 6.5 Open the app

**CLI:**

```
kubectl get service frontend --namespace legallens
```

Copy the address under `EXTERNAL-IP`. It shows `<pending>` for a minute or two while Azure creates it.

**Web interface:** cluster → **Kubernetes resources → Services and ingresses**, row `frontend`, column **External IP**.

Open `http://<EXTERNAL-IP>` in a browser. Use `http://`, not `https://`: there is no certificate yet.

### 6.6 Create the demo user

The database starts empty. Create the demo user and the sample documents:

```
kubectl exec --namespace legallens deploy/backend -- python -m app.seed
```

It logs one line per sample document, then prints the email (`dana@example.com`) and password to sign in with. The password is the `LL_SEED_PASSWORD` you put in the secret.
Ignore the `curl localhost:8000` examples it prints: they are for running the backend on your laptop. In the cluster, sign in at `http://<EXTERNAL-IP>`.

> **Run the seed before you try to sign in.** Until then the user does not exist and the login page says *That email and password do not match an account*.
> Running the seed again is safe: it skips the user and documents that already exist, and it does **not** change an existing user's password.

Forgot the password? Read it back from the secret:

```zsh
kubectl get secret backend-secrets --namespace legallens -o jsonpath='{.data.LL_SEED_PASSWORD}' | base64 -d; echo
```

> **First question is slow.** The AI models (about 4 GB) download to the disk the first time they are needed.
> The first upload or question can take several minutes. Later restarts reuse the downloaded copy.

### If something goes wrong

| Symptom | Command to run | Common cause |
|---|---|---|
| Pod stuck in `Pending` | `kubectl describe pod --namespace legallens -l app=backend` | Not enough memory on the node, or the disk is still being created |
| `ImagePullBackOff` | `kubectl describe pod --namespace legallens -l app=backend` | Wrong ACR name in the YAML, or the cluster was created without `--attach-acr`. Fix with `az aks update --resource-group rg-legallens --name aks-legallens --attach-acr legallensacrXXXX` |
| `CrashLoopBackOff` | `kubectl logs --namespace legallens deploy/backend` | A missing secret value; the log names it |
| Page loads but sign-in fails | `kubectl logs --namespace legallens deploy/frontend` | The backend is not ready yet, or `BACKEND_URL` is wrong |
| *That email and password do not match an account* | `kubectl exec --namespace legallens deploy/backend -- python -m app.seed` | The seed has not run yet (step 6.6), or the password is not the `LL_SEED_PASSWORD` from the secret |
| `zsh: parse error near '\n'` | (none) | A `<...>` placeholder is still in the command (step 6.2) |

---

## 7. Create an identity for GitHub Actions

The CD pipeline has to log in to Azure to push images and update the cluster.
We use **OpenID Connect (OIDC)**: Azure trusts tokens that GitHub issues for our repository and branch, so **no password or secret is stored in GitHub**.

You need three things: an app registration (the identity), a federated credential (the trust rule), and two role assignments (what it may do).

| Permission | Scope | Lets the pipeline |
|---|---|---|
| `AcrPush` | the registry | Push new images |
| `Azure Kubernetes Service Cluster User Role` | the cluster | Download the cluster's kubeconfig and run `kubectl` |

### Web interface

1. Search for **Microsoft Entra ID** → **App registrations** → **New registration**.
   Name `gh-legallens-cd`, leave the rest at defaults, **Register**.
2. On the overview page, copy the **Application (client) ID** and the **Directory (tenant) ID**. You need both in step 8.
3. **Certificates & secrets** → **Federated credentials** tab → **Add credential**:
   - **Federated credential scenario:** `GitHub Actions deploying Azure resources`
   - **Organization:** your GitHub user or org. **Repository:** `LegalLens`
   - **Entity type:** `Branch`. **GitHub branch name:** `main`
   - **Name:** `github-main` → **Add**
4. Give it push access to the registry: **Container registries → legallensacrXXXX → Access control (IAM) → Add → Add role assignment**.
   Role `AcrPush` → **Members:** User, group, or service principal → **Select members** → search `gh-legallens-cd` → **Review + assign**.
5. Give it access to the cluster: **Kubernetes services → aks-legallens → Access control (IAM) → Add → Add role assignment**.
   Role `Azure Kubernetes Service Cluster User Role` → member `gh-legallens-cd` → **Review + assign**.

### Azure CLI

Create the app registration and its service principal:

```
az ad app create --display-name gh-legallens-cd --query appId --output tsv
```

Copy the printed **appId**, then:

```
az ad sp create --id <appId>
```

Create a file `github-credentials.json` with your GitHub user or org in place of `<owner>`
(file names matching `*credentials*.json` are already ignored by `.gitignore`):

```json
{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<owner>/LegalLens:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}
```

Add the trust rule:

```
az ad app federated-credential create --id <appId> --parameters github-credentials.json
```

> **If the pipeline fails with `AADSTS700213: No matching federated identity record found`.** Newer GitHub repositories send the subject with numeric IDs, for example
> `repo:<owner>@49059857/LegalLens@1397180072:ref:refs/heads/main`. The failed `azure/login` step prints the exact value under **subject claim**.
> Add a second credential with that subject (a new `name`, for example `github-main-ids`, and the subject copied exactly), using the same command. Keeping both is fine.

Look up the IDs of the registry and the cluster, and copy each one:

```
az acr show --name legallensacrXXXX --query id --output tsv
az aks show --resource-group rg-legallens --name aks-legallens --query id --output tsv
```

Assign the two roles:

```
az role assignment create --assignee <appId> --role AcrPush --scope <ACR id>
az role assignment create --assignee <appId> --role "Azure Kubernetes Service Cluster User Role" --scope <AKS id>
```

Finally, print the tenant and subscription IDs for step 8:

```
az account show --query "{tenantId:tenantId, subscriptionId:id}" --output table
```

---

## 8. Add the Azure IDs to GitHub as secrets

| Secret name | Value |
|---|---|
| `AZURE_CLIENT_ID` | the app registration's Application (client) ID (`appId`) |
| `AZURE_TENANT_ID` | your Directory (tenant) ID |
| `AZURE_SUBSCRIPTION_ID` | your subscription ID |

None of these is a password; with OIDC they only say *which* identity to use.
We store them as secrets anyway so they do not show up in logs.

### Web interface

On GitHub, open the repository → **Settings → Secrets and variables → Actions → New repository secret**. Add each of the three.

### GitHub CLI

```
gh secret set AZURE_CLIENT_ID --body "<appId>"
gh secret set AZURE_TENANT_ID --body "<tenantId>"
gh secret set AZURE_SUBSCRIPTION_ID --body "<subscriptionId>"
```

The backend's own secrets (`LL_BLOB_KEY` and the others) stay in the Kubernetes secret from step 6.2.
The pipeline never needs to see them.

---

## 9. The CD pipeline

`.github/workflows/cd.yml` repeats steps 5 and 6.4 on every merge to `main`, after CI passes.

```
 CI passes on main
        │
        ▼
 images (backend and frontend in parallel)
   azure/login with OIDC → az acr login → docker build → push legallens-<service>:<commit SHA>
        │
        ▼
 deploy
   aks-set-context → apply k8s/namespace.yaml → check backend-secrets exists
   → envsubst + kubectl apply k8s/*.yaml → kubectl rollout status
```

| Part | What it does |
|---|---|
| Trigger | `workflow_run` on the `CI` workflow, only when it succeeded on `main`. Also **Actions → CD → Run workflow** for a manual run on `main`. |
| Login | `azure/login` with the three secrets from step 8. GitHub's OIDC token must match the federated credential `repo:<owner>/LegalLens:ref:refs/heads/main`, so manual runs from other branches are refused by Azure. |
| Images | Built on the GitHub runner with the same Dockerfiles and the same build cache as CI, for `linux/amd64`, then pushed to ACR. |
| Tag | The commit SHA that CI tested, for example `legallens-backend:3f9c2e1…`. There is no `latest` tag: every pod points to one exact commit. |
| Deploy | Applies `k8s/` with that tag. Kubernetes only rolls out when the tag changes, so each new commit always triggers a rollout. |
| Wait | `kubectl rollout status`, up to 15 minutes for the backend (it may download models) and 5 for the frontend. A deploy that never gets healthy fails the job. |
| Result | The job summary shows the commit and the app's `http://<public IP>`. On failure, it prints the pod state and the backend's last log lines. |

**Before the first run:**

- [ ] Steps 6.1 and 6.2 are done: the `backend-secrets` secret exists. The pipeline checks this and stops with a clear error if it is missing. It never sees the secret values.
- [ ] Steps 7 and 8 are done: the identity, its two roles and the three GitHub secrets.
- [ ] In `.github/workflows/cd.yml`, `ACR_NAME` and `ACR_LOGIN_SERVER` match your registry.
- [ ] `cd.yml` is merged into `main`. GitHub only runs a `workflow_run` trigger from the default branch.

**Expect a short outage on each deploy.** The backend uses `Recreate`: the old pod stops and releases the disk before the new one starts, so the API is down for a minute or two.

**Roll back** to the previous version:

```
kubectl rollout undo deployment/backend --namespace legallens
kubectl rollout undo deployment/frontend --namespace legallens
```

The next merge to `main` deploys its own commit again.

> **Why plain YAML and not Helm?** We have two services and one environment, so there is nothing to template except the image tag, and `envsubst` does that.
> Helm or Kustomize start to pay off once there is a second environment (staging) with different values.

---

## 10. What we left out on purpose

This setup is enough to learn the flow. Before real users, each of these needs doing:

| Left out | Why it matters | What to add |
|---|---|---|
| HTTPS and a domain name | Passwords and cookies travel in plain text over HTTP | An ingress controller, a DNS name and a certificate (for example cert-manager with Let's Encrypt), then `LL_COOKIE_SECURE=true` |
| A managed database | SQLite on one disk means one backend replica and no automatic backups | Azure Database for PostgreSQL; the backend only needs a new `LL_DATABASE_URL` |
| A vector database server | Embedded Qdrant locks its folder, which also blocks scaling | A Qdrant server, set through `LL_AI_QDRANT_URL` |
| Secret management | Secrets typed into `kubectl` are hard to rotate and audit | Azure Key Vault with the Secrets Store CSI driver |
| More than one node | One node is one point of failure | A second node pool and more replicas, once the two rows above are done |
| Monitoring and alerts | You only notice failures when users tell you | Container insights / Azure Monitor |
| Separate staging environment | Every merge goes straight to the only environment | A second namespace or cluster, with a manual approval before production |
| Backups of the disk | Losing the disk loses all documents | Azure Backup for AKS, or disk snapshots |

---

## 11. Clean up

The cluster's VM costs money every hour it exists, even when nobody uses the app.
Roughly, the setup above costs a few dollars per day; check the Azure pricing calculator for your region.

**Pause the cluster** (keeps everything, stops paying for the VM):

```
az aks stop --resource-group rg-legallens --name aks-legallens
az aks start --resource-group rg-legallens --name aks-legallens
```

In the portal: open the cluster → **Overview → Stop** (and **Start** later).

**Delete everything** when you are done. This also deletes the `MC_...` group, the disk and all stored documents:

```
az group delete --name rg-legallens --yes --no-wait
```

In the portal: **Resource groups → rg-legallens → Delete resource group**, type the name to confirm.

The app registration lives in Entra ID, not in the resource group, so delete it separately:

```
az ad app delete --id <appId>
```

In the portal: **Microsoft Entra ID → App registrations → gh-legallens-cd → Delete**.
