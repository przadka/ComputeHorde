# validator

ComputeHorde Validator. See repositories main README for explanation of what that means.

- - -

Skeleton of this project was generated with `cookiecutter-rt-django`, which sometimes gets upgrades that are easy to retrofit into already older projects.

# Base requirements

- docker
- docker-compose
- python 3.11

# Setup development environment

```sh
# 1st tab
$ python -m venv compute_horde_validator
$ source compute_horde_validator
$ ./setup-dev.sh
```

```sh
# 2nd tab
docker-compose up
```

```sh
# 1st tab
cd app/src
python manage.py wait_for_database --timeout 10
python manage.py migrate
python manage.py runserver
```

# Setup production environment (git deployment)

This sets up "deployment by pushing to git storage on remote", so that:

- `git push origin ...` just pushes code to Github / other storage without any consequences;
- `git push production master` pushes code to a remote server running the app and triggers a git hook to redeploy the application.

```
Local .git ------------> Origin .git
                \
                 ------> Production .git (redeploy on push)
```

- - -

Use `ssh-keygen` to generate a key pair for the server, then add read-only access to repository in "deployment keys" section (`ssh -A` is easy to use, but not safe).

```sh
# remote server
mkdir -p ~/repos
cd ~/repos
git init --bare --initial-branch=master validator.git

mkdir -p ~/domains/validator
```

```sh
# locally
git remote add production root@<server>:~/repos/validator.git
git push production master
```

```sh
# remote server
cd ~/repos/validator.git

cat <<'EOT' > hooks/post-receive
#!/bin/bash
unset GIT_INDEX_FILE
export ROOT=/root
export REPO=validator
while read oldrev newrev ref
do
    if [[ $ref =~ .*/master$ ]]; then
        export GIT_DIR="$ROOT/repos/$REPO.git/"
        export GIT_WORK_TREE="$ROOT/domains/$REPO/"
        git checkout -f master
        cd $GIT_WORK_TREE
        ./deploy.sh
    else
        echo "Doing nothing: only the master branch may be deployed on this server."
    fi
done
EOT

chmod +x hooks/post-receive
./hooks/post-receive
cd ~/domains/validator
./setup-prod.sh

# adjust the `.env` file

mkdir letsencrypt
./letsencrypt_setup.sh
./deploy.sh
```

### Deploy another branch

Only `master` branch is used to redeploy an application.
If one wants to deploy other branch, force may be used to push desired branch to remote's `master`:

```sh
git push --force production local-branch-to-deploy:master
```

# Monitoring

Running the app requires proper certificates to be put into `nginx/monitoring_certs`, see `README` located there.

## Monitoring execution time of code blocks

Somewhere, probably in `metrics.py`:

```python
some_calculation_time = prometheus_client.Histogram(
    'some_calculation_time',
    'How Long it took to calculate something',
    namespace='django',
    unit='seconds',
    labelnames=['task_type_for_example'],
    buckets=[0.5, 1, *range(2, 30, 2), *range(30, 75, 5), *range(75, 135, 15)]
)
```

Somewhere else:

```python
with some_calculation_time.labels('blabla').time():
    do_some_work()
```

# Setting up periodic backups

Add to crontab:

```sh
# crontab -e
30 0 * * * cd ~/domains/validator && ./bin/backup-db.sh > ~/backup.log 2>&1
```

Set `BACKUP_LOCAL_ROTATE_KEEP_LAST` to keep only a specific number of most recent backups in local `.backups` directory.

## Configuring offsite targets for backups

Backups are put in `.backups` directory locally, additionally then can be stored offsite in following ways:

**Backblaze**

Set in `.env` file:

- `BACKUP_B2_BUCKET_NAME`
- `BACKUP_B2_KEY_ID`
- `BACKUP_B2_KEY_SECRET`

**Email**

Set in `.env` file:

- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_TARGET`
