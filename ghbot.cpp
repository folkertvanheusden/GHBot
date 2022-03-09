#include <libircclient.h>
#include <libirc_rfcnumeric.h>
#include <mosquitto.h>

#include "error.h"
#include "str.h"


const std::string default_server  = "irc.knageroe.nl";
const std::string default_channel = "#test";

void event_connect(irc_session_t * session, const char * event, const char * origin, const char ** params, unsigned int count)
{
	if (irc_cmd_join(session, default_channel.c_str(), nullptr))
		error_exit(false, "event_connect: failed to join channel: %s", irc_strerror(irc_errno(session)));
}

void event_numeric(irc_session_t * session, unsigned int event, const char * origin, const char ** params, unsigned int count)
{
}

void event_privmsg(irc_session_t * session, const char * event, const char * origin, const char ** params, unsigned int count)
{
	std::vector<std::string> line_parts   = split(params[1], " ");

	for(auto & word : line_parts) {
		std::string match_word = str_tolower(word);

		if (word.substr(0, 4) != "http:" && word.substr(0, 5) != "https:")
			continue;

		if (!mqtt_session) {


		}


	}
}

void event_notice(irc_session_t * session, const char * event, const char * origin, const char ** params, unsigned int count)
{
}

void event_kick(irc_session_t * session, const char * event, const char * origin, const char ** params, unsigned int count)
{
	// it could've been some-one else. then this join is a no-op.
	if (irc_cmd_join(session, default_channel.c_str(), nullptr))
		error_exit(false, "event_kick: failed to join channel: %s", irc_strerror(irc_errno(session)));
}

struct mosquitto *start_mosquitto()
{
	mosquitto_lib_init();

	struct mosquitto *mqtt_session = mosquitto_new(nullptr, nullptr, nullptr);

	if (!mqtt_session)
		error_exit(false, "start_mosquitto: failed setup mosquitto session");

	int err = mosquitto_connect(mqtt_session, mqtt_server.c_str(), 1883, 10);
	if (err != MOSQ_ERR_SUCCESS)
		error_exit(false, "start_mosquitto: failed to connect to MQTT server: %s", mosquitto_strerror(err));

	std::thread mosquitto_thread([mqtt_session] {
			int err = mosquitto_loop_forever(mqtt_session, -1, 1);

			error_exit(false, "start_mosquitto: mosquitto_loop_forever returned unexpectedly: %s", mosquitto_strerror(err));
			});

	mosquitto_thread.detach();

	return mqtt_session;
}

int main(int argc, char *argv[])
{
	struct mosquitto *mosquitto_session = start_mosquitto();

	irc_callbacks_t callbacks { 0 };

	callbacks.event_connect = event_connect;
	callbacks.event_numeric = event_numeric;
	callbacks.event_privmsg = event_privmsg;
	callbacks.event_notice  = event_notice;
	callbacks.event_kick    = event_kick;

	irc_session_t *session = irc_create_session(&callbacks);
	if (!session)
		error_exit(false, "Failed to create irc session");

	if (irc_connect(session, default_server.c_str(), 6667, 0, "ghbot", "ghbot", "GHBot"))
		error_exit(false, "Failed to connect to IRC server: %s", irc_strerror(irc_errno(session)));

	if (irc_run(session))
		error_exit(false, "Failed to run IRC session: %s", irc_strerror(irc_errno(session)));

	irc_destroy_session(session);

	return 0;
}
