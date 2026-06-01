pipeline {
    agent any

    options {
        skipDefaultCheckout(true)
        timestamps()
    }

    parameters {
        string(
            name: 'BITBUCKET_HTTP_BASE_URL',
            defaultValue: 'http://bitbucket-cl.external.svc',
            description: 'Base URL HTTP de Bitbucket Server/Data Center'
        )
        string(
            name: 'INGEST_API_BASE_URL',
            defaultValue: 'https://repositories-kdb.coipo.ist-ia4.npe-k8s.chl.bns',
            description: 'URL alcanzable del API desplegado en Kubernetes'
        )
        string(
            name: 'INGEST_PROVIDER',
            defaultValue: 'bitbucket',
            description: 'Campo provider para /repos/ingest'
        )
        string(
            name: 'INGEST_STARTUP_TIMEOUT_SECONDS',
            defaultValue: '300',
            description: 'Timeout para esperar /health'
        )
        string(
            name: 'INGEST_JOB_TIMEOUT_SECONDS',
            defaultValue: '1800',
            description: 'Timeout para esperar la finalizacion del job'
        )
        string(
            name: 'INGEST_POLL_INTERVAL_SECONDS',
            defaultValue: '10',
            description: 'Intervalo de polling del job'
        )
        string(
            name: 'INGEST_LOGS_TAIL',
            defaultValue: '50',
            description: 'Valor logs_tail para /jobs/{job_id}'
        )
        text(
            name: 'REPO_REGISTRY_JSON',
            defaultValue: '''{
        "_defaults": {
            "auth_deployment": "server",
            "auth_transport": "https",
            "auth_method": "http_basic",
            "embedding_provider": "vertex",
            "embedding_model": "text-embedding-005"
        },
    "COIPO/repositories-kdb": {
        "enabled": true,
        "credentials_type": "http_basic",
            "credentials_id": "bitbucket-coipo-repositories-kdb-http"
    },
    "COIPO/documents-kdb": {
        "enabled": true,
        "credentials_type": "http_basic",
            "credentials_id": "bitbucket-coipo-documents-kdb-http"
    }
}''',
            description: 'Registro central de repos permitidos y sus credenciales.'
        )
        string(
            name: 'INGEST_EMBEDDING_PROVIDER',
            defaultValue: 'vertex',
            description: 'Override opcional de embedding_provider'
        )
        string(
            name: 'INGEST_EMBEDDING_MODEL',
            defaultValue: 'text-embedding-005',
            description: 'Override opcional de embedding_model'
        )
        string(
            name: 'INGEST_AUTH_DEPLOYMENT',
            defaultValue: 'server',
            description: 'auth.deployment cuando uses credenciales HTTP'
        )
        string(
            name: 'INGEST_AUTH_TRANSPORT',
            defaultValue: 'https',
            description: 'auth.transport cuando uses credenciales HTTP'
        )
        string(
            name: 'INGEST_AUTH_METHOD',
            defaultValue: 'http_basic',
            description: 'auth.method cuando uses credenciales HTTP'
        )
    }

    triggers {
        GenericTrigger(
            genericVariables: [
                [key: 'bb_event_key', value: '$.eventKey'],
                [key: 'bb_target_branch', value: '$.pullRequest.toRef.displayId'],
                [key: 'bb_source_branch', value: '$.pullRequest.fromRef.displayId'],
                [key: 'bb_project_key', value: '$.pullRequest.toRef.repository.project.key'],
                [key: 'bb_repo_slug', value: '$.pullRequest.toRef.repository.slug'],
                [key: 'bb_clone_url_1', value: '$.pullRequest.toRef.repository.links.clone[0].href'],
                [key: 'bb_clone_url_2', value: '$.pullRequest.toRef.repository.links.clone[1].href'],
                [key: 'bb_latest_commit', value: '$.pullRequest.toRef.latestCommit']
            ],
            causeString: 'Bitbucket webhook: $bb_event_key -> $bb_target_branch',
            token: 'kdb-rag-ingest',
            printContributedVariables: true,
            printPostContent: false,
            silentResponse: false,
            regexpFilterText: '$bb_event_key $bb_target_branch',
            regexpFilterExpression: '^pr:merged (main|master)$'
        )
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Validar webhook') {
            steps {
                script {
                    if (!(env.bb_event_key == 'pr:merged' &&
                        ['main', 'master'].contains(env.bb_target_branch))) {
                        currentBuild.result = 'NOT_BUILT'
                        error(
                            'Webhook ignorado: ' +
                            "event=${env.bb_event_key}, " +
                            "target=${env.bb_target_branch}"
                        )
                    }

                    if (!params.INGEST_API_BASE_URL?.trim()) {
                        error('INGEST_API_BASE_URL es obligatorio')
                    }

                    def repoRegistry
                    try {
                        repoRegistry = new groovy.json.JsonSlurperClassic()
                            .parseText(params.REPO_REGISTRY_JSON ?: '{}')
                    } catch (Exception exc) {
                        error("REPO_REGISTRY_JSON invalido: ${exc.message}")
                    }

                    def repoKey = "${env.bb_project_key}/${env.bb_repo_slug}"
                    def repoConfig = repoRegistry[repoKey]
                    def repoDefaults = repoRegistry['_defaults']
                    if (!(repoDefaults instanceof Map)) {
                        repoDefaults = [:]
                    }
                    if (!(repoConfig instanceof Map) ||
                        repoConfig.enabled == false) {
                        currentBuild.result = 'NOT_BUILT'
                        error("Repo no registrado o deshabilitado: ${repoKey}")
                    }

                    def cloneCandidates = [
                        env.bb_clone_url_1,
                        env.bb_clone_url_2,
                    ].findAll { it?.trim() }
                    def httpClone = cloneCandidates.find {
                        it ==~ /^https?:\/\/.*/
                    }

                    if (!httpClone) {
                        if (!env.bb_project_key?.trim() ||
                            !env.bb_repo_slug?.trim()) {
                            error(
                                'No se pudo resolver la URL HTTP del repo ' +
                                'desde el payload del webhook'
                            )
                        }
                        httpClone = (
                            params.BITBUCKET_HTTP_BASE_URL.replaceAll('/+$', '') +
                            "/scm/${env.bb_project_key}/${env.bb_repo_slug}.git"
                        )
                    }

                    env.EFFECTIVE_REPO_KEY = repoKey
                    env.EFFECTIVE_REPO_URL = httpClone
                    env.EFFECTIVE_BRANCH = env.bb_target_branch
                    env.EFFECTIVE_COMMIT = env.bb_latest_commit ?: ''
                    env.REPO_CREDENTIALS_TYPE =
                        String.valueOf(repoConfig.credentials_type ?: 'none')
                    env.REPO_CREDENTIALS_ID =
                        String.valueOf(repoConfig.credentials_id ?: '')
                    env.INGEST_AUTH_DEPLOYMENT =
                        String.valueOf(
                            repoConfig.auth_deployment ?: repoDefaults.auth_deployment
                                ?: params.INGEST_AUTH_DEPLOYMENT
                        )
                    env.INGEST_AUTH_TRANSPORT =
                        String.valueOf(
                            repoConfig.auth_transport ?: repoDefaults.auth_transport
                                ?: params.INGEST_AUTH_TRANSPORT
                        )
                    env.INGEST_AUTH_METHOD =
                        String.valueOf(
                            repoConfig.auth_method ?: repoDefaults.auth_method
                                ?: params.INGEST_AUTH_METHOD
                        )
                    env.INGEST_EMBEDDING_PROVIDER =
                        String.valueOf(
                            repoConfig.embedding_provider ?:
                            repoDefaults.embedding_provider ?:
                            params.INGEST_EMBEDDING_PROVIDER
                        )
                    env.INGEST_EMBEDDING_MODEL =
                        String.valueOf(
                            repoConfig.embedding_model ?:
                            repoDefaults.embedding_model ?:
                            params.INGEST_EMBEDDING_MODEL
                        )

                    echo "repo_key=${env.EFFECTIVE_REPO_KEY}"
                    echo "repo_url=${env.EFFECTIVE_REPO_URL}"
                    echo "branch=${env.EFFECTIVE_BRANCH}"
                    echo "commit=${env.EFFECTIVE_COMMIT}"
                    echo "credentials_type=${env.REPO_CREDENTIALS_TYPE}"
                }
            }
        }

        stage('Disparar ingesta') {
            steps {
                script {
                    def bindings = []
                    if (env.REPO_CREDENTIALS_TYPE == 'token') {
                        if (!env.REPO_CREDENTIALS_ID?.trim()) {
                            error(
                                'El repo requiere credentials_type=token ' +
                                'pero no define credentials_id'
                            )
                        }
                        bindings << string(
                            credentialsId: env.REPO_CREDENTIALS_ID,
                            variable: 'INGEST_TOKEN'
                        )
                    }
                    if (env.REPO_CREDENTIALS_TYPE == 'http_basic') {
                        if (!env.REPO_CREDENTIALS_ID?.trim()) {
                            error(
                                'El repo requiere credentials_type=http_basic ' +
                                'pero no define credentials_id'
                            )
                        }
                        bindings << usernamePassword(
                            credentialsId: env.REPO_CREDENTIALS_ID,
                            usernameVariable: 'INGEST_AUTH_USERNAME',
                            passwordVariable: 'INGEST_AUTH_SECRET'
                        )
                    }

                    if (!(env.REPO_CREDENTIALS_TYPE in ['none', 'token', 'http_basic'])) {
                        error(
                            'credentials_type invalido para repo centralizado: ' +
                            env.REPO_CREDENTIALS_TYPE
                        )
                    }

                    if (bindings.isEmpty()) {
                        sh '''
                          set -euo pipefail
                          python3 scripts/trigger_repo_ingest.py
                        '''
                        return
                    }

                    withCredentials(bindings) {
                        sh '''
                          set -euo pipefail
                          python3 scripts/trigger_repo_ingest.py
                        '''
                    }
                }
            }
        }
    }

    post {
        notBuilt {
            echo 'Webhook ignorado porque no correspondia a un merge hacia main o master.'
        }
    }
}