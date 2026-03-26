document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".btn-delete").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var runId = btn.getAttribute("data-run-id");
            if (runId && confirm("Delete this run?")) {
                fetch("/runs/" + runId + "/delete", {method: "POST"})
                    .then(function (r) {
                        return r.ok ? (window.location = "/runs") : alert("Delete failed");
                    });
            }
        });
    });
    document.querySelectorAll(".btn-delete-req").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var runId = btn.getAttribute("data-run-id");
            var requestId = btn.getAttribute("data-request-id");
            if (runId && requestId && confirm("Delete this request?")) {
                fetch("/runs/" + runId + "/requests/" + requestId + "/delete", {method: "POST"})
                    .then(function (r) {
                        return r.ok ? (window.location = "/runs/" + runId) : alert("Delete failed");
                    });
            }
        });
    });
});
