#!/usr/bin/env bash
set -e
unset private_flag source_link target_link target_token

exit_script() { #Обработка выхода из скрипта
  set +x
  unset err_stat
  case "${1,,}" in
    err)  shift; echo -e "\033[1;33mError!\n${@}\033[0m"; err_stat=yes ;;
    warn) shift; echo -e "\033[1;34mWarning!\n${@}\033[0m";;
    info) shift; echo -e "$@" ;;
  esac
  [ -z "${err_stat}" ] && exit 0
  [ -n "${err_stat}" -a "${err_stat}" = yes ] && exit 1
}

check_inputs() { #Проверка аргументов запуска, зависимостей и движков API
  if ! command -v jq &>/dev/null; then
      exit_script err "jq is required but not installed. Please install jq first."
  fi

  t_help="$0 - A tool for mass creation of serial repositories between Github, Gitlab and Gitea.

  Usage: $0 [OPTIONS]
  Options:
    -e, --source_link URL     URL of source organization or user
    -l, --target_link URL     URL of target hub organization or user
    -p, --private             Make mirrored repositories private (default: public)
    -t, --target-token TOKEN  Authentication token for target hub
    -h, --help                Show this help message

  Example:
    $0 -e https://github.com/kubernetes -l https://gitea.example.com/myorg --private -t ghp_xyz123
    $0 -e https://opendev.org/openstack -l https://gitlab.local.com/openstack -t ghp_abc456"

  while [ ! -z "${1}" ]; do
    case "${1,,}" in
      -e|--source_link)
        shift
        [ -z "${1}" ] && exit_script err "The link to the external organization is not specified!"
        source_link_url="${1}"
        source_link_domain="$(getdomain ${1})"
        source_link_location="$(getlocation ${1})"
        [ -z "${source_link_domain}" -o -z "${source_link_location}" ] && \
        exit_script err "Error in external link: ${1}"
        shift;;
      -l|--target_link)
        shift
        [ -z "${1}" ] && exit_script err "The link to the local gitea organization is not specified!"
        gitea_local_domain="$(getdomain ${1})"
        gitea_local_location="$(getlocation ${1})"
        gitea_target_link="${1##*/}"
        [ -z "${gitea_local_domain}" -o -z "${gitea_local_location}" ] && \
        exit_script err "Error in gitea link: ${1}"
        shift;;
      -p|--private)
        private_flag="true"
        shift;;
      -t|--target-token)
        shift
        [ -z "${1}" ] && exit_script err "The token of the local gitea is not specified!"
        target_token="${1}"
        shift;;
      -h|--help)
        exit_script info "${t_help}";;
      *) exit_script err "Error flag - ${1}\n\n${t_help}";;
    esac
  done

  source_type="$(detect_vendor ${source_link_domain})"
  dest_type="$(detect_vendor ${source_link_domain})"
}

getdomain() { #Получить доменную часть. Например, из https://opendev.org/openstack получить opendev.org
  echo "$1" | sed -E 's|^https?://([^/]+).*|\1|'
}
getlocation() { #Получить остаток от доменной части. Например, из https://opendev.org/openstack получить openstack
  echo "$1" | sed -E 's|^https?://[^/]+||'
}

detect_vendor(link) { #Определение варианта API по указанному адресу
    if [ "${link}" = "github.com" ]; then
        echo "github"
        return
    elif [ "$(timeout 2 curl -sLk -w '%{http_code}' -o /dev/null "https://${link}/api/v4")" = 200 ]; then
        echo "gitlab"
        return
    elif [ "$(timeout 2 curl -sLk -w '%{http_code}' -o /dev/null "https://${link}/api/v1/version")" = 200 ]; then
        echo "gitea"
        return
    else
        exit_script err "unknown vendor on ${link}"
    fi
}


# Получение UID организации в Gitea
gitea_local_uid=$(curl -s -H "Authorization: token ${target_token}" \
    "${gitea_local_domain}/api/v1/orgs/${gitea_target_link}" | jq -r '.id')

if [[ "${gitea_local_uid}" == "null" || -z "${gitea_local_uid}" ]]; then
    exit_script err "Cannot get Gitea org ID for ${gitea_target_link}. Please check the organization name and token permissions."
fi

echo "Gitea org ID: ${gitea_local_uid}"

# Function to check if repository exists in target Gitea
repo_exists() {
    local repo_name="$1"
    local response=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: token ${target_token}" \
        "${gitea_local_domain}/api/v1/repos/${gitea_target_link}/${repo_name}")
    [ "$response" -eq "200" ]
}

if [ "$source_type" = "github" ]; then
    # GitHub API handling
    count_repo=$(curl -s "https://api.github.com/users/${github_org}" | jq -r '.public_repos')
    count_repo_list=$(( $(curl -s "https://api.github.com/users/${github_org}" | jq -r '.public_repos') / 100 + 1 ))
    
    for p in $(seq 1 ${count_repo_list}); do
        repos=$(curl -s "https://api.github.com/users/${github_org}/repos?page=${p}&per_page=100&type=all" | jq -r '.[].name')
        if [[ -z "$repos" ]]; then
            exit_script warn "No repositories found for GitHub org: ${github_org}"
            exit 0
        fi
        
        for repo in $repos; do
            if repo_exists "$repo"; then
                echo "Repository ${repo} already exists in target Gitea, skipping..."
                continue
            fi
            
            echo "Creating mirror for: ${repo}"
            clone_url="https://github.com/${github_org}/${repo}.git"
            
            curl -s -X POST "${gitea_local_domain}/api/v1/repos/migrate" \
                -H "Authorization: token ${target_token}" \
                -H "Content-Type: application/json" \
                -d @- <<EOF
{
    "clone_addr": "${clone_url}",
    "repo_name": "${repo}",
    "mirror": true,
    "private": ${private_flag:-false},
    "wiki": true,
    "uid": ${gitea_local_uid}
}
EOF
            echo "Mirror created for ${repo}"
        done
    done
else
    # Gitea API handling
    page=1
    while true; do
        repos=$(curl -s "https://${source_link_domain}/api/v1/orgs/${gitea_source_link}/repos?page=${page}&limit=100" | jq -r '.[].name')
        if [[ -z "$repos" ]]; then
            break
        fi
        
        for repo in $repos; do
            if repo_exists "$repo"; then
                echo "Repository ${repo} already exists in target Gitea, skipping..."
                continue
            fi
            
            echo "Creating mirror for: ${repo}"
            clone_url="https://${source_link_domain}/${gitea_source_link}/${repo}.git"
            
            curl -s -X POST "${gitea_local_domain}/api/v1/repos/migrate" \
                -H "Authorization: token ${target_token}" \
                -H "Content-Type: application/json" \
                -d @- <<EOF
{
    "clone_addr": "${clone_url}",
    "repo_name": "${repo}",
    "mirror": true,
    "private": ${private_flag},
    "wiki": true,
    "uid": ${gitea_local_uid}
}
EOF
            echo "Mirror created for ${repo}"
        done
        
        page=$((page + 1))
    done
fi

echo "Mirroring process completed."


check_inputs
detect_vendor
