# -*- coding: utf-8 -*-
from telegram.ext import Updater, CommandHandler, Job, MessageHandler, Filters
from pymongo import MongoClient
import pymongo
import logging, re, datetime, os, uuid

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

pattern_add = re.compile(
    '/([a-zA-Zа-яА-Я]{1,15})((\s[^@\s]+)+)(\s@[a-zA-Z0-9]+)?((\sчерез|in)((\s\d{1,2}\s[a-zA-Zа-яА-Я]+)+))?',
    re.IGNORECASE)

pattern_list = re.compile('/([a-zA-Zа-яА-Я]{1,15})(\s@[a-zA-Zа-яА-Я]*)?')
pattern_done = re.compile('/([a-zA-Zа-яА-Я]{1,15})\s(\d{1,2})(\s@[a-zA-Zа-яА-Я]*)?')
pattern_reassign = re.compile('/([a-zA-Zа-яА-Я]{1,15})\s(\d{1,2})\s(@[a-zA-Zа-яА-Я]+)\s(on|на)\s(@[a-zA-Zа-яА-Я]*)')

time_units_hours = re.compile('(\d{1,2})\s(ч(ас)?(а|ов)?)')
time_units_min = re.compile('(\d{1,2})\s(м(ин)?(ут)?(ы)?)')
time_units_sec = re.compile('(\d{1,2})\s(с(ек)?(унд)?(ы|у)?)')

assignee_all_name = 'all'

error_text = 'Не понял'
add_response_text = 'Добавлена заметка {} для {}'
add_reminder_response_text = 'Добавлено напоминание {} для {}, напомню через {}'

client = MongoClient("mongodb://localhost:27017")
storage = client.cuckudoo.issues


class Issue(object):
    def __init__(self, text, owner, created, assignee=None, interval=None):
        self.text = text
        self.owner = owner
        self.created = created
        self.assignee = assignee
        self.interval = interval

    def __str__(self, *args, **kwargs):
        return "text={}, owner={}, created={}, assignee={}, interval={}".format(self.text, self.owner, self.created,
                                                                                self.assignee, self.interval)

    def to_dict(self):
        return {'_id': self._id, 'text': self.text, 'owner': self.owner,
                'created': self.created, 'assignee': self.assignee,
                'interval': self.interval, 'done': self.done}

    def from_dict(dict):
        issue = Issue(dict['text'], dict['owner'], dict['created'], dict['assignee'], dict['interval'])
        issue.done = dict['done']
        return issue

    def format(self, idx):
        return '{}{}. {} @{}\n\r'.format(("\u2705" if self.done is not None else "\uD83D\uDCCC"),
                                         str(idx), self.text, self.assignee)

    def format_list(issues_dict):
        idx = 0
        result = ''

        for issue in issues_dict:
            idx += 1
            result += Issue.from_dict(issue).format(idx)

        return result


def add(bot, update, job_queue):
    match = pattern_add.match(update.message.text)

    if not match:
        logger.info('message invalid')
        update.message.reply_text(error_text)
        return

    text = match.group(2).strip()
    assignee = match.group(4)
    interval_value = match.group(7)
    owner = update.message.chat.id

    issue = Issue(text, owner, datetime.datetime.today())
    issue._id = uuid.uuid4()
    issue.done = None

    if interval_value is not None:
        interval_value = interval_value.strip()
        interval_sec = 0
        if time_units_hours.search(interval_value):
            interval_sec += int(time_units_hours.search(interval_value).group(1)) * 3600
        if time_units_min.search(interval_value):
            interval_sec += int(time_units_min.search(interval_value).group(1)) * 60
        if time_units_sec.search(interval_value):
            interval_sec += int(time_units_sec.search(interval_value).group(1))
        issue.interval = interval_sec

    if assignee is not None:
        issue.assignee = assignee.strip().replace('@', '')
    else:
        issue.assignee = assignee_all_name

    storage.insert_one(issue.to_dict())
    logger.info('Add issue ' + str(issue))

    if issue.interval is None:
        update.message.reply_text(add_response_text.format(issue.text, issue.assignee))
    else:
        job = Job(alarm, issue.interval, repeat=False, context=issue._id)
        job_queue.put(job)
        update.message.reply_text(add_reminder_response_text.format(issue.text, issue.assignee, interval_value))


def alarm(bot, job):
    for issue_dict in storage.find({'_id': job.context}):
        issue = Issue.from_dict(issue_dict)
        bot.sendMessage(issue.owner, text=issue.text)
        return


def list(bot, update):
    match = pattern_list.match(update.message.text)
    if not match:
        logger.info('message invalid')
        update.message.reply_text(error_text)
        return

    assignee = match.group(2)
    owner = update.message.chat.id
    if assignee is not None:
        assignee = assignee.strip().replace('@', '')
        issues_list = storage.find({'assignee': assignee, 'owner': owner}).sort('created', pymongo.ASCENDING)
    else:
        issues_list = storage.find({'owner': owner}).sort('created', pymongo.ASCENDING)

    output = Issue.format_list(issues_list)

    update.message.reply_text(output)


def done(bot, update):
    match = pattern_done.match(update.message.text)
    if not match:
        logger.info('message invalid')
        update.message.reply_text(error_text)
        return

    issue_index = int(match.group(2).strip())
    assignee = match.group(3)
    owner = update.message.chat.id
    if assignee is not None:
        assignee = assignee.strip().replace('@', '')
    else:
        assignee = assignee_all_name

    issue_dicts = storage.find({'assignee': assignee, 'owner': owner}).sort('created', pymongo.ASCENDING)

    if issue_dicts.count() < issue_index:
        update.message.reply_text(error_text)
        return
    else:
        issue_id = issue_dicts[issue_index - 1]['_id']

    storage.update_one({'_id': issue_id}, {'$set': {'done': True}})
    output = Issue.format_list(storage.find({'assignee': assignee, 'owner': owner}).sort('created', pymongo.ASCENDING))
    update.message.reply_text(output)


def reassign(bot, update):
    match = pattern_reassign.match(update.message.text)
    if not match:
        logger.info('message invalid')
        update.message.reply_text(error_text)
        return

    issue_index = int(match.group(2).strip())
    assignee_old = match.group(3).strip().replace('@', '')
    assignee_new = match.group(5).strip().replace('@', '')
    owner = update.message.chat.id

    issue_dicts = storage.find({'assignee': assignee_old, 'owner': owner}).sort('created', pymongo.ASCENDING)

    if issue_dicts.count() < issue_index:
        update.message.reply_text(error_text)
        return
    else:
        issue_id = issue_dicts[issue_index - 1]['_id']

    logger.info('reassign issue {} from {} to {}', issue_index, assignee_old, assignee_new)

    storage.update_one({'_id': issue_id}, {'$set': {'assignee': assignee_new}})
    output = Issue.format_list(
        storage.find({'assignee': assignee_new, 'owner': owner}).sort('created', pymongo.ASCENDING))
    update.message.reply_text(output)


def delete(bot, update):
    match = pattern_done.match(update.message.text)
    if not match:
        logger.info('message invalid')
        update.message.reply_text(error_text)
        return

    issue_index = int(match.group(2).strip())
    assignee = match.group(3)
    owner = update.message.chat.id
    if assignee is not None:
        assignee = assignee.strip().replace('@', '')
    else:
        assignee = assignee_all_name

    issue_dicts = storage.find({'assignee': assignee, 'owner': owner}).sort('created', pymongo.ASCENDING)

    if issue_dicts.count() < issue_index:
        update.message.reply_text(error_text)
        return
    else:
        issue_id = issue_dicts[issue_index - 1]['_id']

    storage.delete_one({'_id': issue_id})
    output = Issue.format_list(storage.find({'assignee': assignee, 'owner': owner}).sort('created', pymongo.ASCENDING))
    update.message.reply_text(output)


def start(bot, update):
    update.message.reply_text('Hi!')


def help(bot, update):
    update.message.reply_text('Help!')


def error(bot, update, error):
    logger.warn('Update "%s" caused error "%s"' % (update, error))


def main():
    token = os.environ['TOKEN']
    # Create the EventHandler and pass it your bot's token.
    updater = Updater(token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))

    dp.add_handler(CommandHandler("help", help))
    dp.add_handler(CommandHandler("help@cuckudoobot", help))
    dp.add_handler(CommandHandler("п", help))
    dp.add_handler(CommandHandler("помощь", help))
    dp.add_handler(CommandHandler("хелп", help))
    dp.add_handler(CommandHandler("памагите", help))
    dp.add_handler(CommandHandler("ничегонепонимаю", help))

    dp.add_handler(CommandHandler("add", add, pass_job_queue=True))
    dp.add_handler(CommandHandler("добавить", add, pass_job_queue=True))
    dp.add_handler(CommandHandler("задача", add, pass_job_queue=True))
    dp.add_handler(CommandHandler("еще", add, pass_job_queue=True))
    dp.add_handler(CommandHandler("напомни", add, pass_job_queue=True))
    dp.add_handler(CommandHandler("напомнить", add, pass_job_queue=True))

    dp.add_handler(CommandHandler("list", list))
    dp.add_handler(CommandHandler("list@cuckudoobot", list))
    dp.add_handler(CommandHandler("с", list))
    dp.add_handler(CommandHandler("список", list))
    dp.add_handler(CommandHandler("все", list))
    dp.add_handler(CommandHandler("всё", list))

    dp.add_handler(CommandHandler("done", done))
    dp.add_handler(CommandHandler("г", done))
    dp.add_handler(CommandHandler("готово", done))
    dp.add_handler(CommandHandler("готов", done))
    dp.add_handler(CommandHandler("сделаль", done))
    dp.add_handler(CommandHandler("сделать", done))
    dp.add_handler(CommandHandler("выполнено", done))
    dp.add_handler(CommandHandler("разделался", done))

    dp.add_handler(CommandHandler("del", delete))
    dp.add_handler(CommandHandler("delete", delete))
    dp.add_handler(CommandHandler("у", delete))
    dp.add_handler(CommandHandler("удалить", delete))
    dp.add_handler(CommandHandler("убрать", delete))

    dp.add_handler(CommandHandler("reassign", reassign))
    dp.add_handler(CommandHandler("assignee", reassign))
    dp.add_handler(CommandHandler("назначить", reassign))
    dp.add_handler(CommandHandler("переназначить", reassign))
    dp.add_handler(CommandHandler("перевестистрелки", reassign))

    # # on noncommand i.e message - echo the message on Telegram
    # dp.add_handler(MessageHandler(Filters.text, echo))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()
