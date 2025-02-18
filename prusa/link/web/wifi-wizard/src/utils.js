    const PROBE_TIMEOUT = 2000;

    export let requestUrl = window.location.href.split('/').slice(0, -1).join('/');

    export function handleFormData(e) {
        // getting the action url
        const ACTION_URL = e.target.action

        // get the form fields data and convert it to URLSearchParams
        const formData = new FormData(e.target)
        const data = new URLSearchParams()
        for (let field of formData) {
            const [key, value] = field
            data.append(key, value)
        }

        // check the form's method and send the fetch accordingly
        if (e.target.method.toLowerCase() == 'get') {
            fetcher(`${ACTION_URL}?${data}`);
        } else {
        fetcher(ACTION_URL, {
            method: 'POST',
            body: data
        })
        }
    };

    export function changeHost(probeUrl){
        window.location.href = probeUrl + "/wifi";
    }

    export function turnOffHotspot(){
        fetcher(requestUrl + "/wifi/api/hotspot_not_needed", {method: 'POST'});
    }

    const instanceFingerprint = document.getElementById("instance-fingerprint").value;

    function addFingerprint(options) {
        const update = { ...options };
        update.headers = {
                ...update.headers,
                "X-Instance-Fingerprint": instanceFingerprint
            };
        return update;
    }

    export async function probe(url) {
        try {
            const response = await fetcher(url + "/wifi/api/probe", {method: 'HEAD', signal: AbortSignal.timeout(PROBE_TIMEOUT)})
            return response.status == 200;
        } catch {
            return false;
        }
    }

    export function fetcher(url, options) {
        return fetch(url, addFingerprint(options));
    }
