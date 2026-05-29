(() => {
    const client = window.DisasterApiClient;

    const api = {
        list: () => client.request('/notification-channels'),
        get: (id) => client.request('/notification-channels/' + id),
        update: (id, data) => client.request('/notification-channels/' + id, {
            method: 'PUT',
            body: data,
        }),
        test: (id, data) => client.request('/notification-channels/' + id + '/test', {
            method: 'POST',
            body: data || {},
        }),
    };

    window.NotifyChannelsApi = api;
})();
