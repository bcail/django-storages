import gzip
import mimetypes
from datetime import datetime
from datetime import timedelta
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone
from google.cloud.exceptions import NotFound
from google.cloud.storage.blob import Blob
from google.cloud.storage.retry import DEFAULT_RETRY

from storages.backends import gcloud
from tests.utils import NonSeekableContentFile


class GCloudTestCase(TestCase):
    def setUp(self):
        self.bucket_name = 'test_bucket'
        self.filename = 'test_file.txt'
        self.storage = gcloud.GoogleCloudStorage(bucket_name=self.bucket_name)


class GCloudStorageTests(GCloudTestCase):
    def setUp(self):
        super().setUp()
        self.client_patcher = mock.patch('storages.backends.gcloud.Client')
        self.client_patcher.start()

    def tearDown(self):
        self.client_patcher.stop()

    def test_open_read(self):
        """
        Test opening a file and reading from it
        """
        data = b'This is some test read data.'

        f = self.storage.open(self.filename)
        self.storage._client.bucket.assert_called_with(self.bucket_name)
        self.storage._bucket.get_blob.assert_called_with(
            self.filename, chunk_size=None)

        f.blob.download_to_file = lambda tmpfile: tmpfile.write(data)
        self.assertEqual(f.read(), data)

    def test_open_read_num_bytes(self):
        data = b'This is some test read data.'
        num_bytes = 10

        f = self.storage.open(self.filename)
        self.storage._client.bucket.assert_called_with(self.bucket_name)
        self.storage._bucket.get_blob.assert_called_with(
            self.filename, chunk_size=None)

        f.blob.download_to_file = lambda tmpfile: tmpfile.write(data)
        self.assertEqual(f.read(num_bytes), data[0:num_bytes])

    def test_open_read_nonexistent(self):
        self.storage._bucket = mock.MagicMock()
        self.storage._bucket.get_blob.return_value = None

        self.assertRaises(FileNotFoundError, self.storage.open, self.filename)
        self.storage._bucket.get_blob.assert_called_with(
            self.filename, chunk_size=None)

    def test_open_read_nonexistent_unicode(self):
        filename = 'ủⓝï℅ⅆℇ.txt'

        self.storage._bucket = mock.MagicMock()
        self.storage._bucket.get_blob.return_value = None

        self.assertRaises(FileNotFoundError, self.storage.open, filename)

    @mock.patch('storages.backends.gcloud.Blob')
    def test_open_write(self, MockBlob):
        """
        Test opening a file and writing to it
        """
        data = 'This is some test write data.'

        # Simulate the file not existing before the write
        self.storage._bucket = mock.MagicMock()
        self.storage._bucket.get_blob.return_value = None
        self.storage.default_acl = 'projectPrivate'

        f = self.storage.open(self.filename, 'wb')
        MockBlob.assert_called_with(self.filename, self.storage._bucket, chunk_size=None)

        f.write(data)
        tmpfile = f._file
        # File data is not actually written until close(), so do that.
        f.close()

        MockBlob().upload_from_file.assert_called_with(
            tmpfile, rewind=True,
            content_type=mimetypes.guess_type(self.filename)[0],
            retry=DEFAULT_RETRY,
            predefined_acl='projectPrivate')

    def test_save(self):
        data = 'This is some test content.'
        content = ContentFile(data)

        self.storage.save(self.filename, content)

        self.storage._client.bucket.assert_called_with(self.bucket_name)
        self.storage._bucket.get_blob().upload_from_file.assert_called_with(
            content,
            rewind=True,
            retry=DEFAULT_RETRY,
            size=len(data),
            content_type=mimetypes.guess_type(self.filename)[0],
            predefined_acl=None
        )

    def test_save2(self):
        data = 'This is some test ủⓝï℅ⅆℇ content.'
        filename = 'ủⓝï℅ⅆℇ.txt'
        content = ContentFile(data)

        self.storage.save(filename, content)

        self.storage._client.bucket.assert_called_with(self.bucket_name)
        self.storage._bucket.get_blob().upload_from_file.assert_called_with(
            content, rewind=True, retry=DEFAULT_RETRY, size=len(data), content_type=mimetypes.guess_type(filename)[0],
            predefined_acl=None)

    def test_save_with_default_acl(self):
        data = 'This is some test ủⓝï℅ⅆℇ content.'
        filename = 'ủⓝï℅ⅆℇ.txt'
        content = ContentFile(data)

        # ACL Options
        # 'projectPrivate', 'bucketOwnerRead', 'bucketOwnerFullControl',
        # 'private', 'authenticatedRead', 'publicRead', 'publicReadWrite'
        self.storage.default_acl = 'publicRead'

        self.storage.save(filename, content)

        self.storage._client.bucket.assert_called_with(self.bucket_name)
        self.storage._bucket.get_blob().upload_from_file.assert_called_with(
            content, rewind=True, retry=DEFAULT_RETRY, size=len(data), content_type=mimetypes.guess_type(filename)[0],
            predefined_acl='publicRead')

    def test_delete(self):
        self.storage.delete(self.filename)

        self.storage._client.bucket.assert_called_with(self.bucket_name)
        self.storage._bucket.delete_blob.assert_called_with(self.filename, retry=DEFAULT_RETRY)

    def test_exists(self):
        self.storage._bucket = mock.MagicMock()
        self.assertTrue(self.storage.exists(self.filename))
        self.storage._bucket.get_blob.assert_called_with(self.filename)

        self.storage._bucket.reset_mock()
        self.storage._bucket.get_blob.return_value = None
        self.assertFalse(self.storage.exists(self.filename))
        self.storage._bucket.get_blob.assert_called_with(self.filename)

    def test_exists_no_bucket(self):
        # exists('') should return False if the bucket doesn't exist
        self.storage._client = mock.MagicMock()
        self.storage._client.get_bucket.side_effect = NotFound('dang')
        self.assertFalse(self.storage.exists(''))

    def test_exists_bucket(self):
        # exists('') should return True if the bucket exists
        self.assertTrue(self.storage.exists(''))

    def test_listdir(self):
        file_names = ["some/path/1.txt", "2.txt", "other/path/3.txt", "4.txt"]
        subdir = ""

        self.storage._bucket = mock.MagicMock()
        blobs, prefixes = [], []
        for name in file_names:
            directory = name.rsplit("/", 1)[0]+"/" if "/" in name else ""
            if directory == subdir:
                blob = mock.MagicMock(spec=Blob)
                blob.name = name.split("/")[-1]
                blobs.append(blob)
            else:
                prefixes.append(directory.split("/")[0]+"/")

        return_value = mock.MagicMock()
        return_value.__iter__ = mock.MagicMock(return_value=iter(blobs))
        return_value.prefixes = prefixes
        self.storage._bucket.list_blobs.return_value = return_value

        dirs, files = self.storage.listdir('')

        self.assertEqual(len(dirs), 2)
        for directory in ["some", "other"]:
            self.assertTrue(directory in dirs,
                            """ "{}" not in directory list "{}".""".format(
                                directory, dirs))

        self.assertEqual(len(files), 2)
        for filename in ["2.txt", "4.txt"]:
            self.assertTrue(filename in files,
                            """ "{}" not in file list "{}".""".format(
                                filename, files))

    def test_listdir_subdir(self):
        file_names = ["some/path/1.txt", "some/2.txt"]
        subdir = "some/"

        self.storage._bucket = mock.MagicMock()
        blobs, prefixes = [], []
        for name in file_names:
            directory = name.rsplit("/", 1)[0] + "/"
            if directory == subdir:
                blob = mock.MagicMock(spec=Blob)
                blob.name = name.split("/")[-1]
                blobs.append(blob)
            else:
                prefixes.append(directory.split(subdir)[1])

        return_value = mock.MagicMock()
        return_value.__iter__ = mock.MagicMock(return_value=iter(blobs))
        return_value.prefixes = prefixes
        self.storage._bucket.list_blobs.return_value = return_value

        dirs, files = self.storage.listdir(subdir)

        self.assertEqual(len(dirs), 1)
        self.assertTrue('path' in dirs,
                        """ "path" not in directory list "{}".""".format(dirs))

        self.assertEqual(len(files), 1)
        self.assertTrue('2.txt' in files,
                        """ "2.txt" not in files list "{}".""".format(files))

    def test_size(self):
        size = 1234

        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        blob.size = size
        self.storage._bucket.get_blob.return_value = blob

        self.assertEqual(self.storage.size(self.filename), size)
        self.storage._bucket.get_blob.assert_called_with(self.filename)

    def test_size_no_file(self):
        self.storage._bucket = mock.MagicMock()
        self.storage._bucket.get_blob.return_value = None

        self.assertRaises(NotFound, self.storage.size, self.filename)

    def test_modified_time(self):
        naive_date = datetime(2017, 1, 2, 3, 4, 5, 678)
        aware_date = timezone.make_aware(naive_date, timezone.utc)

        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        blob.updated = aware_date
        self.storage._bucket.get_blob.return_value = blob

        with self.settings(TIME_ZONE='UTC'):
            mt = self.storage.modified_time(self.filename)
            self.assertTrue(timezone.is_naive(mt))
            self.assertEqual(mt, naive_date)
            self.storage._bucket.get_blob.assert_called_with(self.filename)

    def test_get_modified_time(self):
        naive_date = datetime(2017, 1, 2, 3, 4, 5, 678)
        aware_date = timezone.make_aware(naive_date, timezone.utc)

        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        blob.updated = aware_date
        self.storage._bucket.get_blob.return_value = blob

        with self.settings(TIME_ZONE='America/Montreal', USE_TZ=False):
            mt = self.storage.get_modified_time(self.filename)
            self.assertTrue(timezone.is_naive(mt))
            naive_date_montreal = timezone.make_naive(aware_date)
            self.assertEqual(mt, naive_date_montreal)
            self.storage._bucket.get_blob.assert_called_with(self.filename)

        with self.settings(TIME_ZONE='America/Montreal', USE_TZ=True):
            mt = self.storage.get_modified_time(self.filename)
            self.assertTrue(timezone.is_aware(mt))
            self.assertEqual(mt, aware_date)
            self.storage._bucket.get_blob.assert_called_with(self.filename)

    def test_get_created_time(self):
        naive_date = datetime(2017, 1, 2, 3, 4, 5, 678)
        aware_date = timezone.make_aware(naive_date, timezone.utc)

        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        blob.time_created = aware_date
        self.storage._bucket.get_blob.return_value = blob

        with self.settings(TIME_ZONE='America/Montreal', USE_TZ=False):
            mt = self.storage.get_created_time(self.filename)
            self.assertTrue(timezone.is_naive(mt))
            naive_date_montreal = timezone.make_naive(aware_date)
            self.assertEqual(mt, naive_date_montreal)
            self.storage._bucket.get_blob.assert_called_with(self.filename)

        with self.settings(TIME_ZONE='America/Montreal', USE_TZ=True):
            mt = self.storage.get_created_time(self.filename)
            self.assertTrue(timezone.is_aware(mt))
            self.assertEqual(mt, aware_date)
            self.storage._bucket.get_blob.assert_called_with(self.filename)

    def test_modified_time_no_file(self):
        self.storage._bucket = mock.MagicMock()
        self.storage._bucket.get_blob.return_value = None

        self.assertRaises(NotFound, self.storage.modified_time, self.filename)

    def test_url_public_object(self):
        url = 'https://example.com/mah-bukkit/{}'.format(self.filename)
        self.storage.default_acl = 'publicRead'

        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        blob.public_url = url
        blob.generate_signed_url = 'not called'
        self.storage._bucket.blob.return_value = blob

        self.assertEqual(self.storage.url(self.filename), url)
        self.storage._bucket.blob.assert_called_with(self.filename)

    def test_url_not_public_file(self):
        secret_filename = 'secret_file.txt'
        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        generate_signed_url = mock.MagicMock(return_value='http://signed_url')
        blob.public_url = 'http://this_is_public_url'
        blob.generate_signed_url = generate_signed_url
        self.storage._bucket.blob.return_value = blob

        url = self.storage.url(secret_filename)
        self.storage._bucket.blob.assert_called_with(secret_filename)
        self.assertEqual(url, 'http://signed_url')
        blob.generate_signed_url.assert_called_with(
            expiration=timedelta(seconds=86400), version="v4"
        )

    def test_url_not_public_file_with_custom_expires(self):
        secret_filename = 'secret_file.txt'
        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        generate_signed_url = mock.MagicMock(return_value='http://signed_url')
        blob.generate_signed_url = generate_signed_url
        self.storage._bucket.blob.return_value = blob

        self.storage.expiration = timedelta(seconds=3600)

        url = self.storage.url(secret_filename)
        self.storage._bucket.blob.assert_called_with(secret_filename)
        self.assertEqual(url, 'http://signed_url')
        blob.generate_signed_url.assert_called_with(
            expiration=timedelta(seconds=3600), version="v4"
        )

    def test_custom_endpoint(self):
        self.storage.custom_endpoint = "https://example.com"

        self.storage.default_acl = 'publicRead'
        url = "{}/{}".format(self.storage.custom_endpoint, self.filename)
        self.assertEqual(self.storage.url(self.filename), url)

        bucket_name = "hyacinth"
        self.storage.default_acl = 'projectPrivate'
        self.storage._bucket = mock.MagicMock()
        blob = mock.MagicMock()
        generate_signed_url = mock.MagicMock()
        blob.bucket = mock.MagicMock()
        type(blob.bucket).name = mock.PropertyMock(return_value=bucket_name)
        blob.generate_signed_url = generate_signed_url
        self.storage._bucket.blob.return_value = blob
        self.storage.url(self.filename)
        blob.generate_signed_url.assert_called_with(
            bucket_bound_hostname=self.storage.custom_endpoint,
            expiration=timedelta(seconds=86400),
            version="v4",
        )

    def test_get_available_name(self):
        self.storage.file_overwrite = True
        self.assertEqual(self.storage.get_available_name(
            self.filename), self.filename)

        self.storage._bucket = mock.MagicMock()
        self.storage._bucket.get_blob.return_value = None
        self.storage.file_overwrite = False
        self.assertEqual(self.storage.get_available_name(
            self.filename), self.filename)
        self.storage._bucket.get_blob.assert_called_with(self.filename)

    def test_get_available_name_unicode(self):
        filename = 'ủⓝï℅ⅆℇ.txt'
        self.assertEqual(self.storage.get_available_name(filename), filename)

    def test_cache_control(self):
        data = 'This is some test content.'
        filename = 'cache_control_file.txt'
        content = ContentFile(data)
        cache_control = 'public, max-age=604800'

        self.storage.cache_control = cache_control
        self.storage.save(filename, content)

        bucket = self.storage.client.bucket(self.bucket_name)
        blob = bucket.get_blob(filename)
        self.assertEqual(blob.cache_control, cache_control)

    def test_storage_save_gzip_twice(self):
        """
        Test saving the same file content twice with gzip enabled.
        """
        # Given
        self.storage.gzip = True
        name = 'test_storage_save.css'
        content = ContentFile("I should be gzip'd")

        # When
        self.storage.save(name, content)
        self.storage.save('test_storage_save_2.css', content)

        # Then
        self.storage._client.bucket.assert_called_with(self.bucket_name)
        obj = self.storage._bucket.get_blob()
        self.assertEqual(obj.content_encoding, 'gzip')
        obj.upload_from_file.assert_called_with(
            mock.ANY,
            rewind=False,
            retry=DEFAULT_RETRY,
            size=None,
            predefined_acl=None,
            content_type='text/css',
        )
        args, kwargs = obj.upload_from_file.call_args
        content = args[0]
        zfile = gzip.GzipFile(mode='rb', fileobj=content)
        self.assertEqual(zfile.read(), b"I should be gzip'd")

    def test_compress_content_len(self):
        """
        Test that file returned by _compress_content() is readable.
        """
        self.storage.gzip = True
        content = ContentFile("I should be gzip'd")
        content = self.storage._compress_content(content)
        self.assertTrue(len(content.read()) > 0)

    def test_location_leading_slash(self):
        msg = (
            "GoogleCloudStorage.location cannot begin with a leading slash. "
            "Found '/'. Use '' instead."
        )
        with self.assertRaises(ImproperlyConfigured, msg=msg):
            gcloud.GoogleCloudStorage(location='/')

    def test_override_settings(self):
        with override_settings(GS_LOCATION='foo1'):
            storage = gcloud.GoogleCloudStorage()
            self.assertEqual(storage.location, 'foo1')
        with override_settings(GS_LOCATION='foo2'):
            storage = gcloud.GoogleCloudStorage()
            self.assertEqual(storage.location, 'foo2')

    def test_override_class_variable(self):
        class MyStorage1(gcloud.GoogleCloudStorage):
            location = 'foo1'

        storage = MyStorage1()
        self.assertEqual(storage.location, 'foo1')

        class MyStorage2(gcloud.GoogleCloudStorage):
            location = 'foo2'

        storage = MyStorage2()
        self.assertEqual(storage.location, 'foo2')

    def test_override_init_argument(self):
        storage = gcloud.GoogleCloudStorage(location='foo1')
        self.assertEqual(storage.location, 'foo1')
        storage = gcloud.GoogleCloudStorage(location='foo2')
        self.assertEqual(storage.location, 'foo2')

    def test_dupe_file_chunk_size(self):
        """
        Tests that recreating a file that already exists in the bucket
        respects the `GS_BLOB_CHUNK_SIZE` setting
        """
        chunk_size = 1024*256

        with override_settings(GS_BLOB_CHUNK_SIZE=chunk_size):
            # Creating a new storage here since chunk-size is set as an
            # attribute on init
            storage = gcloud.GoogleCloudStorage()
            storage._bucket = mock.MagicMock()
            # Confirms that `get_blob` always returns a truthy value
            storage._bucket.get_blob.return_value = True

            storage.open(self.filename, 'wb')
            storage._bucket.get_blob.assert_called_with(
                self.filename, chunk_size=chunk_size)


class GoogleCloudGzipClientTests(GCloudTestCase):
    def setUp(self):
        super().setUp()
        self.storage.gzip = True

    @mock.patch('google.cloud.storage.blob.Blob._do_upload')
    @mock.patch('google.auth.default', return_value=['foo', None])
    def test_storage_save_gzipped(self, *args):
        """
        Test saving a gzipped file
        """
        name = 'test_storage_save.js.gz'
        content = ContentFile("I am gzip'd", name=name)

        blob = Blob('x', None)
        blob.upload_from_file = mock.MagicMock(side_effect=blob.upload_from_file)
        patcher = mock.patch('google.cloud.storage.Bucket.get_blob', return_value=blob)
        try:
            patcher.start()
            self.storage.save(name, content)
            blob.upload_from_file.assert_called_with(
                mock.ANY,
                rewind=False,
                retry=DEFAULT_RETRY,
                size=None,
                predefined_acl=None,
                content_type='application/javascript'
            )
        finally:
            patcher.stop()

    @mock.patch('google.cloud.storage.blob.Blob._do_upload')
    @mock.patch('google.auth.default', return_value=['foo', None])
    def test_storage_save_gzipped_non_seekable(self, *args):
        """
        Test saving a gzipped file
        """
        name = 'test_storage_save.gz'
        content = NonSeekableContentFile("I am gzip'd")

        blob = Blob('x', None)
        blob.upload_from_file = mock.MagicMock(side_effect=blob.upload_from_file)
        patcher = mock.patch('google.cloud.storage.Bucket.get_blob', return_value=blob)
        try:
            patcher.start()
            self.storage.save(name, content)
            blob.upload_from_file.assert_called_with(
                mock.ANY,
                rewind=False,
                retry=DEFAULT_RETRY,
                size=11,
                predefined_acl=None,
                content_type=None
            )
        finally:
            patcher.stop()

    @mock.patch('google.cloud.storage.blob.Blob._do_upload')
    @mock.patch('google.auth.default', return_value=['foo', None])
    def test_storage_save_gzip(self, *args):
        """
        Test saving a file with gzip enabled.
        """
        self.storage.gzip = True
        name = 'test_storage_save.css'
        content = ContentFile("I should be gzip'd")

        blob = Blob('x', None)
        blob.upload_from_file = mock.MagicMock(side_effect=blob.upload_from_file)
        patcher = mock.patch('google.cloud.storage.Bucket.get_blob', return_value=blob)

        try:
            patcher.start()
            self.storage.save(name, content)
            obj = self.storage._bucket.get_blob()
            obj.upload_from_file.assert_called_with(
                mock.ANY,
                rewind=False,
                retry=DEFAULT_RETRY,
                size=None,
                predefined_acl=None,
                content_type='text/css',
            )
            args, kwargs = obj.upload_from_file.call_args
            content = args[0]
            zfile = gzip.GzipFile(mode='rb', fileobj=content)
            self.assertEqual(zfile.read(), b"I should be gzip'd")
        finally:
            patcher.stop()
