# -*- coding: utf-8 -*-
"""
    celery.app.control
    ~~~~~~~~~~~~~~~~~~~

    Client for worker remote control commands.
    Server implementation is in :mod:`celery.worker.control`.

"""
from __future__ import absolute_import
from __future__ import with_statement

from kombu.pidbox import Mailbox
from kombu.utils import cached_property

from . import app_or_default


def flatten_reply(reply):
    nodes = {}
    for item in reply:
        nodes.update(item)
    return nodes


class Inspect(object):
    app = None

    def __init__(self, destination=None, timeout=1, callback=None,
            connection=None, app=None):
        self.app = app or self.app
        self.destination = destination
        self.timeout = timeout
        self.callback = callback
        self.connection = connection

    def _prepare(self, reply):
        if not reply:
            return
        by_node = flatten_reply(reply)
        if self.destination and \
                not isinstance(self.destination, (list, tuple)):
            return by_node.get(self.destination)
        return by_node

    def _request(self, command, **kwargs):
        return self._prepare(self.app.control.broadcast(command,
                                      arguments=kwargs,
                                      destination=self.destination,
                                      callback=self.callback,
                                      connection=self.connection,
                                      timeout=self.timeout, reply=True))

    def report(self):
        return self._request('report')

    def active(self, safe=False):
        return self._request('dump_active', safe=safe)

    def scheduled(self, safe=False):
        return self._request('dump_schedule', safe=safe)

    def reserved(self, safe=False):
        return self._request('dump_reserved', safe=safe)

    def stats(self):
        return self._request('stats')

    def revoked(self):
        return self._request('dump_revoked')

    def registered(self):
        return self._request('dump_tasks')
    registered_tasks = registered

    def ping(self):
        return self._request('ping')

    def active_queues(self):
        return self._request('active_queues')


class Control(object):
    Mailbox = Mailbox

    def __init__(self, app=None):
        self.app = app_or_default(app)
        self.mailbox = self.Mailbox('celeryd', type='fanout')

    @cached_property
    def inspect(self):
        return self.app.subclass_with_self(Inspect, reverse='control.inspect')

    def purge(self, connection=None):
        """Discard all waiting tasks.

        This will ignore all tasks waiting for execution, and they will
        be deleted from the messaging server.

        :returns: the number of tasks discarded.

        """
        with self.app.default_connection(connection) as conn:
            return self.app.amqp.TaskConsumer(conn).purge()
    discard_all = purge

    def revoke(self, task_id, destination=None, terminate=False,
            signal='SIGTERM', **kwargs):
        """Tell all (or specific) workers to revoke a task by id.

        If a task is revoked, the workers will ignore the task and
        not execute it after all.

        :param task_id: Id of the task to revoke.
        :keyword terminate: Also terminate the process currently working
            on the task (if any).
        :keyword signal: Name of signal to send to process if terminate.
            Default is TERM.

        See :meth:`broadcast` for supported keyword arguments.

        """
        return self.broadcast('revoke', destination=destination,
                              arguments={'task_id': task_id,
                                         'terminate': terminate,
                                         'signal': signal}, **kwargs)

    def ping(self, destination=None, timeout=1, **kwargs):
        """Ping all (or specific) workers.

        Returns answer from alive workers.

        See :meth:`broadcast` for supported keyword arguments.

        """
        return self.broadcast('ping', reply=True, destination=destination,
                              timeout=timeout, **kwargs)

    def rate_limit(self, task_name, rate_limit, destination=None, **kwargs):
        """Tell all (or specific) workers to set a new rate limit
        for task by type.

        :param task_name: Name of task to change rate limit for.
        :param rate_limit: The rate limit as tasks per second, or a rate limit
            string (`'100/m'`, etc.
            see :attr:`celery.task.base.Task.rate_limit` for
            more information).

        See :meth:`broadcast` for supported keyword arguments.

        """
        return self.broadcast('rate_limit', destination=destination,
                              arguments={'task_name': task_name,
                                         'rate_limit': rate_limit},
                              **kwargs)

    def add_consumer(self, queue, exchange=None, exchange_type='direct',
            routing_key=None, options=None, **kwargs):
        """Tell all (or specific) workers to start consuming from a new queue.

        Only the queue name is required as if only the queue is specified
        then the exchange/routing key will be set to the same name (
        like automatic queues do).

        .. note::

            This command does not respect the default queue/exchange
            options in the configuration.

        :param queue: Name of queue to start consuming from.
        :keyword exchange: Optional name of exchange.
        :keyword exchange_type: Type of exchange (defaults to 'direct')
            command to, when empty broadcast to all workers.
        :keyword routing_key: Optional routing key.

        See :meth:`broadcast` for supported keyword arguments.

        """
        return self.broadcast('add_consumer',
                arguments=dict({'queue': queue, 'exchange': exchange,
                                'exchange_type': exchange_type,
                                'routing_key': routing_key}, **options or {}),
                **kwargs)

    def cancel_consumer(self, queue, **kwargs):
        """Tell all (or specific) workers to stop consuming from ``queue``.

        Supports the same keyword arguments as :meth:`broadcast`.

        """
        return self.broadcast('cancel_consumer',
                arguments={'queue': queue}, **kwargs)

    def time_limit(self, task_name, soft=None, hard=None, **kwargs):
        """Tell all (or specific) workers to set time limits for
        a task by type.

        :param task_name: Name of task to change time limits for.
        :keyword soft: New soft time limit (in seconds).
        :keyword hard: New hard time limit (in seconds).

        Any additional keyword arguments are passed on to :meth:`broadcast`.

        """
        return self.broadcast('time_limit',
                              arguments={'task_name': task_name,
                                         'hard': hard, 'soft': soft}, **kwargs)

    def enable_events(self, destination=None, **kwargs):
        """Tell all (or specific) workers to enable events."""
        return self.broadcast('enable_events', {}, destination, **kwargs)

    def disable_events(self, destination=None, **kwargs):
        """Tell all (or specific) workers to enable events."""
        return self.broadcast('disable_events', {}, destination, **kwargs)

    def pool_grow(self, n=1, destination=None, **kwargs):
        """Tell all (or specific) workers to grow the pool by ``n``.

        Supports the same arguments as :meth:`broadcast`.

        """
        return self.broadcast('pool_grow', {}, destination, **kwargs)

    def pool_shrink(self, n=1, destination=None, **kwargs):
        """Tell all (or specific) workers to shrink the pool by ``n``.

        Supports the same arguments as :meth:`broadcast`.

        """
        return self.broadcast('pool_shrink', {}, destination, **kwargs)

    def broadcast(self, command, arguments=None, destination=None,
            connection=None, reply=False, timeout=1, limit=None,
            callback=None, channel=None, **extra_kwargs):
        """Broadcast a control command to the celery workers.

        :param command: Name of command to send.
        :param arguments: Keyword arguments for the command.
        :keyword destination: If set, a list of the hosts to send the
            command to, when empty broadcast to all workers.
        :keyword connection: Custom broker connection to use, if not set,
            a connection will be established automatically.
        :keyword reply: Wait for and return the reply.
        :keyword timeout: Timeout in seconds to wait for the reply.
        :keyword limit: Limit number of replies.
        :keyword callback: Callback called immediately for each reply
            received.

        """
        with self.app.default_connection(connection) as conn:
            arguments = dict(arguments or {}, **extra_kwargs)
            return self.mailbox(conn)._broadcast(command, arguments,
                                                 destination, reply, timeout,
                                                 limit, callback,
                                                 channel=channel)
